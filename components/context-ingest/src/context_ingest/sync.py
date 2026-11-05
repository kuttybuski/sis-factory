"""Source reconciliation and syncing. Shared credentials come from the environment; a team's own connections come from portal-api. Both are only read here."""

import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from sis_context import confluence, db, github
from sis_contracts.connections import Credentials
from sis_context.embed import Embedder
from sis_context.learn import mine_review_lessons, qa_pairs_from_events, review_notes
from sis_context.qa import qa_to_doc

log = logging.getLogger("context_ingest")
QA_EVENTS = ("agent.asked_question", "agent.answered", "wait.timed_out", "task.killed", "task.failed")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    confluence_url: str = field(default_factory=lambda: os.environ.get("CONFLUENCE_BASE_URL") or os.environ.get("JIRA_BASE_URL", ""))
    confluence_email: str = field(default_factory=lambda: os.environ.get("CONFLUENCE_EMAIL") or os.environ.get("JIRA_EMAIL", ""))
    confluence_token: str = field(default_factory=lambda: os.environ.get("CONFLUENCE_API_TOKEN") or os.environ.get("JIRA_API_TOKEN", ""))
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    github_api_url: str = github.API
    credentials: Credentials | None = field(default=None, repr=False)  # where a team's own Jira and GitHub connections come from; None = shared credentials only
    pr_history: int = field(default_factory=lambda: int(os.environ.get("SIS_PR_HISTORY_COUNT", "100")))
    review_min_count: int = field(default_factory=lambda: int(os.environ.get("SIS_REVIEW_MIN_COUNT", "2")))
    trusted_associations: tuple[str, ...] = field(default_factory=lambda: tuple(
        a.strip().upper() for a in os.environ.get("SIS_TRUSTED_ASSOCIATIONS", "OWNER,MEMBER,COLLABORATOR").split(",") if a.strip()))
    portal_url: str = field(default_factory=lambda: os.environ.get("PORTAL_API_URL", "http://portal-api:8000"))
    ingest_token: str = field(default_factory=lambda: os.environ.get("SIS_INGEST_TOKEN", ""))


async def reconcile(pool, teams: list[dict[str, Any]]) -> dict[str, int]:
    """Creates a source per configured Confluence space and GitHub repo, and disables ones no longer configured."""
    wanted: set[tuple[str, str]] = set()
    for t in teams:
        for space in t.get("confluence_spaces") or []:
            wanted.add(("confluence_space", space))
            await db.upsert_source(pool, kind="confluence_space", key=space, team=t["name"])
        for repo in t.get("repos") or []:
            for kind in ("github_prs", "repo_docs"):
                wanted.add((kind, repo))
                await db.upsert_source(pool, kind=kind, key=repo, team=t["name"], repo=repo)
    await db.upsert_source(pool, kind="qa", key="tasks")
    wanted.add(("qa", "tasks"))
    disabled = 0
    for s in await db.list_sources(pool):
        if s["kind"] != "push" and (s["kind"], s["key"]) not in wanted:
            await db.set_source_enabled(pool, s["id"], False)
            disabled += 1
    return {"sources": len(wanted), "disabled": disabled}


async def _qa_docs(pool):
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "select t.id, t.jira_key, t.team, t.repo, e.payload from task_events e join tasks t on t.id = e.task_id "
            "where not t.demo and e.type = any(%s) order by t.id, e.id", (list(QA_EVENTS),))).fetchall()
    by_task: dict[str, dict[str, Any]] = defaultdict(lambda: {"events": []})
    for r in rows:
        d = by_task[r["id"]]
        d.update(jira_key=r["jira_key"], team=r["team"], repo=r["repo"])
        d["events"].append(r["payload"])
    for task_id, d in by_task.items():
        for i, qa in enumerate(qa_pairs_from_events(task_id, d["events"])):
            yield qa_to_doc(qa, i, team=d["team"], repo=d["repo"], jira_key=d["jira_key"])


async def propose_lessons(cfg: Config, repo: str, prs: list) -> int:
    """Recurring reviewer feedback becomes candidate lessons. portal-api always queues these for human review."""
    if not cfg.ingest_token:
        log.info("SIS_INGEST_TOKEN not set; skipping lesson proposals")
        return 0
    sent = 0
    async with httpx.AsyncClient(base_url=cfg.portal_url, headers={"Authorization": f"Bearer {cfg.ingest_token}"}, timeout=15) as c:
        for cand in mine_review_lessons(review_notes(prs, cfg.trusted_associations), min_count=cfg.review_min_count):
            r = await c.post("/api/lessons", json={"text": cand.text, "scope": "repo", "repo": repo, "author": cand.reviewers[0], "author_role": "member",
                                                   "source": "pr_review", "source_url": cand.evidence[0] if cand.evidence else None})
            if r.status_code == 201 and not r.json().get("duplicate"):
                sent += 1
    return sent


async def config_for(cfg: Config, team: str | None) -> Config:
    """The shared configuration, with the team's own Jira site and GitHub token swapped in where the team has connections.
    Raises ConnectionsUnavailable if the portal cannot say, so a source is never synced as the wrong account."""
    if not team or cfg.credentials is None:
        return cfg
    jira, gh = await cfg.credentials.jira(team), await cfg.credentials.github(team)
    if jira:
        cfg = replace(cfg, confluence_url=jira.base_url, confluence_email=jira.email, confluence_token=jira.token)
    if gh:
        cfg = replace(cfg, github_token=gh.token, github_api_url=gh.api_url)
    return cfg


async def run_source(pool, source: dict[str, Any], embedder: Embedder, cfg: Config) -> db.SyncStats:
    kind, key = source["kind"], source["key"]
    if kind in ("confluence_space", "github_prs", "repo_docs"):
        cfg = await config_for(cfg, source.get("team"))
    if kind == "confluence_space":
        if not (cfg.confluence_url and cfg.confluence_email and cfg.confluence_token):
            raise ConfigError("Confluence is not configured: set CONFLUENCE_BASE_URL (or JIRA_BASE_URL), and the matching email and API token.")
        async with confluence.client(cfg.confluence_url, cfg.confluence_email, cfg.confluence_token) as c:
            return await db.sync_docs(pool, source["id"], confluence.fetch_space(c, key, source["team"], skip_restricted=os.environ.get("SIS_CONFLUENCE_RESTRICTED", "skip") != "index"), embedder)
    if kind in ("github_prs", "repo_docs"):
        if not cfg.github_token:
            raise ConfigError("GitHub is not configured: set GITHUB_TOKEN (read access to the repository).")
        async with github.client(cfg.github_token, cfg.github_api_url) as c:
            if kind == "repo_docs":
                return await db.sync_docs(pool, source["id"], github.fetch_repo_docs(c, key, source["team"]), embedder)
            collected: list = []
            stats = await db.sync_docs(pool, source["id"], github.fetch_prs(c, key, source["team"], cfg.pr_history, collect=collected), embedder)
            stats.proposed_lessons = await propose_lessons(cfg, key, collected)
            return stats
    if kind == "qa":
        return await db.sync_docs(pool, source["id"], _qa_docs(pool), embedder)
    raise ConfigError(f"source kind '{kind}' is synced by pushing documents, not by pulling")


async def sync_one(pool, source: dict[str, Any], embedder: Embedder, cfg: Config) -> dict[str, Any]:
    """Runs one source and records the outcome on it. Never raises: the status is the report."""
    try:
        stats = await run_source(pool, source, embedder, cfg)
        await db.record_sync(pool, source["id"], ok=True, error=None, stats=stats)
        return {"id": source["id"], "key": source["key"], "ok": True, **stats.model_dump()}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:500]
        log.warning("sync of %s %s failed: %s", source["kind"], source["key"], msg)
        await db.record_sync(pool, source["id"], ok=False, error=msg, stats=None)
        return {"id": source["id"], "key": source["key"], "ok": False, "error": msg}
