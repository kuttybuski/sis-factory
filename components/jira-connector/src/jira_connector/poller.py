"""Pickup (label -> workflow) and replies (@sis comment -> signal). Safe to run repeatedly: every step is idempotent."""

import logging
import re
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from sis_contracts import Settings
from sis_contracts import lessons as L
from sis_contracts import handling as H
from sis_contracts import policy
from sis_contracts.intake import held_back
from sis_contracts.workflow import SIG_REPLY, Reply, TaskInput

from jira_connector.adf import adf_to_text, clean
from jira_connector.client import JiraClient

log = logging.getLogger("jira_connector")
REPO_LINE = re.compile(r"^\s*repo(?:sitory)?\s*:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*$", re.IGNORECASE | re.MULTILINE)


class Flow(Protocol):
    async def start(self, key: str, inp: TaskInput) -> bool: ...  # False if already running
    async def signal(self, key: str, name: str, arg: Any, namespace: str = "default") -> bool: ...  # False if no running workflow
    async def running(self) -> int: ...


@dataclass
class Config:
    settings: Settings = field(default_factory=Settings)
    allowed_projects: set[str] = field(default_factory=set)  # empty = every project
    responders: dict[str, L.Role] = field(default_factory=dict)  # account id, email or display name -> role
    max_concurrent: int = 5
    bot_account_id: str = ""
    wait_timeout_seconds: int = 7200
    max_run_seconds: int = 14400
    review_poll_seconds: int = 60  # 0 = stop at the draft PR instead of following it to merge

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "Config":
        responders: dict[str, L.Role] = {}
        for role, var in ((L.Role.MEMBER, "SIS_RESPONDERS"), (L.Role.MAINTAINER, "SIS_MAINTAINERS"), (L.Role.TEAM_LEAD, "SIS_TEAM_LEADS"), (L.Role.ADMIN, "SIS_ADMINS")):
            for who in filter(None, (x.strip() for x in env.get(var, "").split(","))):
                responders[who] = role
        s = Settings()
        return cls(settings=s, allowed_projects={p for p in env.get("SIS_ALLOWED_JIRA_PROJECTS", "").split(",") if p}, responders=responders,
                   max_concurrent=int(env.get("SIS_MAX_CONCURRENT_TASKS", "5")), bot_account_id=s.bot_account_id,
                   wait_timeout_seconds=s.wait_timeout_minutes * 60, max_run_seconds=int(env.get("SIS_MAX_RUN_MINUTES", "240")) * 60,
                   review_poll_seconds=int(env.get("SIS_REVIEW_POLL_SECONDS", "60")))


def resolve_role(person: dict, issue_fields: dict, cfg: Config) -> L.Role:
    """Who may talk to the agent. Listed people get their role; with no list at all, the reporter and assignee count as members."""
    ids = {person.get("accountId"), person.get("emailAddress"), person.get("displayName")} - {None, ""}
    for ident in ids:
        if ident in cfg.responders:
            return cfg.responders[ident]
    if not cfg.responders:
        involved = {(issue_fields.get(f) or {}).get("accountId") for f in ("reporter", "assignee")} - {None}
        if person.get("accountId") in involved:
            return L.Role.MEMBER
    return L.Role.NONE


def repo_for(description: str, team: dict | None, summary: str = "", components: list[str] | None = None) -> str | None:
    """The repository a ticket is for: an explicit `Repo: owner/name` line; else the team's only repository; else the one team repository
    named by a Jira component or mentioned in the title or description (by owner/name or by name alone). None when it stays ambiguous."""
    if m := REPO_LINE.search(description):
        return m.group(1)
    repos = (team or {}).get("repos") or []
    if len(repos) == 1:
        return repos[0]
    names = {c.strip().lower() for c in components or []}
    text = f"{summary}\n{description}".lower()
    named = lambda r: r.lower() in names or r.split("/")[-1].lower() in names  # noqa: E731
    mentioned = lambda r: r.lower() in text or re.search(rf"(?<![\w/-]){re.escape(r.split('/')[-1].lower())}(?![\w-])", text) is not None  # noqa: E731
    for test in (named, mentioned):
        hits = [r for r in repos if test(r)]
        if len(hits) == 1:
            return hits[0]
    return None


def team_for(key: str, teams: list[dict]) -> dict | None:
    project = key.split("-")[0]
    return next((t for t in teams if project in (t.get("jira_projects") or [])), None)


def kind_of(issue: dict, cfg: Config) -> str:
    return H.kind_of(((issue["fields"].get("issuetype") or {}).get("name")) or "", cfg.settings.jira_type_map)


# (team name, kind) -> the effective policy, as a dict. None when it could not be fetched; the built-in policy for the kind is used then.
PolicyFor = Callable[[str | None, str], Awaitable[dict | None]]


def build_input(issue: dict, team: dict | None, cfg: Config, resolved: dict | None = None) -> TaskInput | str:
    """A TaskInput, or the reason the ticket cannot be started (posted back to the author)."""
    f, key = issue["fields"], issue["key"]
    kind = kind_of(issue, cfg)
    description = clean(adf_to_text(f.get("description")))
    repo = repo_for(description, team, f.get("summary", ""), [c.get("name", "") for c in f.get("components") or []])
    if not repo:
        options = (team or {}).get("repos") or []
        which = f" This team works on {', '.join(options)}: set the Jira component to one of them, or" if options else ""
        return f"I could not tell which repository this is for.{which} add a line `Repo: owner/name` to the description, then add the label again."
    s = cfg.settings
    return TaskInput(jira_key=key, title=f.get("summary", ""), description=description, reporter=(f.get("reporter") or {}).get("displayName", ""),
                     team=(team or {}).get("name"), namespace=(team or {}).get("temporal_namespace", "default"), repo=repo, labels=f.get("labels", []),
                     budget_usd=s.max_budget_usd, budget_extension_usd=s.budget_extension_usd, wait_timeout_seconds=cfg.wait_timeout_seconds,
                     max_run_seconds=cfg.max_run_seconds, mention=s.mention, max_revisions=s.handling_max_revisions, kind=kind, parent_key=(f.get("parent") or {}).get("key", ""),
                     policy=resolved or H.as_dict(H.resolve(kind)[0]), review_poll_seconds=cfg.review_poll_seconds)


async def pickup_cycle(jira: JiraClient, flow: Flow, teams: list[dict], cfg: Config, *, dry_run: bool = False, policy_for: PolicyFor | None = None, scope: str = "",
                       intake: dict | None = None) -> list[str]:
    """Starts a workflow for each ticket carrying the trigger label, then swaps the label. Returns the keys it started."""
    s = cfg.settings
    jql = f'labels = "{s.label_trigger}" AND labels != "{s.label_picked}"' + (f" AND {scope}" if scope else "") + " ORDER BY created ASC"
    started = []
    for issue in await jira.search(jql, ["summary", "description", "labels", "reporter", "assignee", "issuetype", "parent", "components"], limit=50):
        key, labels = issue["key"], issue["fields"].get("labels", [])
        if not policy.should_pick(labels, s):
            continue
        if cfg.allowed_projects and key.split("-")[0] not in cfg.allowed_projects:
            log.info("ignoring %s: project is not on SIS_ALLOWED_JIRA_PROJECTS", key)
            continue
        team = team_for(key, teams)
        if why := held_back((team or {}).get("name", ""), intake):
            log.info("not starting %s: %s", key, why)
            continue
        resolved = await policy_for((team or {}).get("name"), kind_of(issue, cfg)) if policy_for else None
        built = build_input(issue, team, cfg, resolved)
        if isinstance(built, str):
            await jira.add_comment(key, f"{s.mention} did not start. {built}")
            await jira.update_labels(key, ["sis-needs-info"], [s.label_trigger])
            continue
        if dry_run:
            log.info("DRY RUN: would start %s for %s (%s)", key, built.repo, built.title)
            continue
        if await flow.running() >= cfg.max_concurrent:
            log.info("at the concurrency limit (%d); %s will be picked up later", cfg.max_concurrent, key)
            break
        await flow.start(key, built)  # idempotent: a second start for the same ticket is a no-op
        await jira.update_labels(key, [s.label_picked], [s.label_trigger])
        started.append(key)
        if intake is not None:  # count it now, so one cycle cannot overshoot a team's limits
            n = intake.setdefault("counts", {}).setdefault((team or {}).get("name", ""), {"running": 0, "started_24h": 0})
            n["running"], n["started_24h"] = n.get("running", 0) + 1, n.get("started_24h", 0) + 1
    return started


class Seen:
    """Comment ids already handled. In memory: after a restart comments are re-read, which is harmless (the workflow and lesson store both dedupe)."""

    def __init__(self, cap: int = 5000) -> None:
        self.ids: dict[str, None] = {}
        self.cap = cap

    def add(self, cid: str) -> bool:
        if cid in self.ids:
            return False
        self.ids[cid] = None
        while len(self.ids) > self.cap:
            self.ids.pop(next(iter(self.ids)))
        return True


async def handle_command(cmd: L.Command, jira: JiraClient, portal: httpx.AsyncClient, key: str, comment: dict, role: L.Role, repo: str | None, team: dict | None, cfg: Config) -> None:
    s, author = cfg.settings, comment["author"].get("displayName", "someone")
    if cmd.action == "remember":
        if err := L.text_error(cmd.text, s):
            await jira.add_comment(key, f"{s.mention} could not save that: {err}")
            return
        scope_value = cmd.scope_value or ((team or {}).get("name") if cmd.scope is L.Scope.TEAM else repo)
        r = await portal.post("/api/lessons", json={"text": cmd.text, "scope": cmd.scope.value, "scope_value": scope_value, "repo": repo, "author": author,
                                                    "author_role": role.value, "ticket_key": key, "ttl_days": cmd.ttl_days})
        if r.status_code == 422:
            await jira.add_comment(key, f"{s.mention} could not save that: {r.json().get('detail')}")
            return
        r.raise_for_status()
        les = r.json()
        outcome = {"approved": "Saved and active.", "candidate": "Saved. It will be used once someone approves it on the Learning page.",
                   "rejected": f"Not saved: {les.get('reason')}"}[les["status"]]
        await jira.add_comment(key, f"{s.mention} L{les['id']}: {outcome}" + (" (This was already saved.)" if les.get("duplicate") else ""))
    else:
        r = await portal.get("/api/lessons")
        lesson = next((x for x in r.json() if x["id"] == cmd.lesson_id), None) if r.status_code == 200 else None
        if lesson is None:
            await jira.add_comment(key, f"{s.mention} I cannot find lesson L{cmd.lesson_id}.")
        elif not L.can_forget(author, role, lesson["author"]):
            await jira.add_comment(key, f"{s.mention} only the author or a maintainer can remove L{cmd.lesson_id}.")
        else:
            await portal.post(f"/api/lessons/{cmd.lesson_id}/disable", json={"by": author, "reason": f"forgotten via {key}"})
            await jira.add_comment(key, f"{s.mention} L{cmd.lesson_id} removed.")


async def relay_answer(jira: JiraClient, flow: Flow, team: dict | None, key: str, text: str, by: str, seen: Seen) -> bool:
    """An answer typed on the 3D view. It is posted on the ticket first, so Jira keeps the whole conversation even if Temporal's history is
    lost, then given to the waiting task like a Jira reply (the comment id keeps the workflow and reply_cycle from taking it twice).
    False if the ticket has no running task."""
    cid = await jira.add_comment(key, f"{by} answered in the SIS 3D view:\n\n{text}")
    comment_id = f"{key}:{cid}" if cid else ""
    if comment_id:
        seen.add(comment_id)
    return await flow.signal(key, SIG_REPLY, Reply(text=text, author=by, comment_id=comment_id), namespace=(team or {}).get("temporal_namespace") or "default")


async def reply_cycle(jira: JiraClient, flow: Flow, portal: httpx.AsyncClient, teams: list[dict], cfg: Config, seen: Seen, *, scope: str = "") -> dict[str, int]:
    """Reads new comments on tickets the agent is involved with. Commands are carried out; replies from allowed people are signalled."""
    s = cfg.settings.model_copy(update={"bot_account_id": cfg.bot_account_id or cfg.settings.bot_account_id})  # the account looked up at startup counts for real @mentions
    labels = ", ".join(f'"{x}"' for x in (s.label_picked, s.label_trigger, s.label_timed_out))
    counts = {"replies": 0, "commands": 0, "ignored": 0}
    for issue in await jira.search(f"labels in ({labels}) AND updated >= -2d" + (f" AND {scope}" if scope else ""), ["summary", "description", "labels", "reporter", "assignee"], limit=100):
        key, fields = issue["key"], issue["fields"]
        team = team_for(key, teams)
        repo = repo_for(clean(adf_to_text(fields.get("description"))), team)
        for c in await jira.comments(key):
            if not seen.add(f"{key}:{c['id']}"):
                continue
            who = c["author"]
            if cfg.bot_account_id and who.get("accountId") == cfg.bot_account_id:
                continue  # our own comments
            if not policy.is_for_agent(c["text"], s):
                continue
            role = resolve_role(who, fields, cfg)
            if role is L.Role.NONE:
                log.info("ignoring a comment on %s from %s: not an allowed responder", key, who.get("displayName"))
                counts["ignored"] += 1
                continue
            if cmd := L.parse_command(c["text"], s):  # commands first: "@sis remember ..." is not an answer to a question
                await handle_command(cmd, jira, portal, key, c, role, repo, team, Config(settings=s, responders=cfg.responders))
                counts["commands"] += 1
                continue
            reply = Reply(text=policy.message_for_agent(c["text"], s), author=who.get("displayName", ""), comment_id=f"{key}:{c['id']}")
            if await flow.signal(key, SIG_REPLY, reply, namespace=(team or {}).get("temporal_namespace") or "default"):
                counts["replies"] += 1
    return counts
