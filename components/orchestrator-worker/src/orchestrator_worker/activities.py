"""Activities: every network call the workflow needs, except Jira (those live in jira-connector, the only holder of Jira credentials)."""

import os
import time
from dataclasses import dataclass

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from injection_guard import fingerprint, scan, wrap_untrusted
from sis_contracts import Settings
from sis_contracts.workflow import TaskInput

from orchestrator_worker import classify


@dataclass
class Deps:
    portal: httpx.AsyncClient  # carries the ingest token
    gateway: httpx.AsyncClient  # carries the internal token
    provisioner: httpx.AsyncClient
    broker: httpx.AsyncClient
    context: httpx.AsyncClient
    settings: Settings
    sandbox_gateway_url: str = "http://sandbox-gateway:8080"  # as the sandbox sees it
    sandbox_image: str = "localhost/sis-agent-runner:latest"
    agent: str = "scripted"


_deps: Deps | None = None


def set_deps(d: Deps) -> None:
    global _deps
    _deps = d


def deps() -> Deps:
    if _deps is None:
        raise RuntimeError("activities used before set_deps()")
    return _deps


def internal_clients(env: dict[str, str] | None = None) -> Deps:
    e = os.environ if env is None else env
    auth = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
    return Deps(
        portal=httpx.AsyncClient(base_url=e.get("PORTAL_API_URL", "http://portal-api:8000"), headers=auth(e.get("SIS_INGEST_TOKEN", "")), timeout=15),
        gateway=httpx.AsyncClient(base_url=e.get("SANDBOX_GATEWAY_URL", "http://sandbox-gateway:8080"), headers=auth(e.get("SIS_INTERNAL_TOKEN", "")), timeout=30),
        provisioner=httpx.AsyncClient(base_url=e.get("SANDBOX_PROVISIONER_URL", "http://sandbox-provisioner:8080"), headers=auth(e.get("SIS_INTERNAL_TOKEN", "")), timeout=120),
        broker=httpx.AsyncClient(base_url=e.get("GIT_BROKER_URL", "http://git-broker:8080"), headers=auth(e.get("SIS_INTERNAL_TOKEN", "")), timeout=300),
        context=httpx.AsyncClient(base_url=e.get("CONTEXT_API_URL", "http://context-api:8080"), timeout=60),
        settings=Settings(), sandbox_gateway_url=e.get("SANDBOX_URL_FOR_AGENT", "http://sandbox-gateway:8080"),
        sandbox_image=e.get("SIS_SANDBOX_IMAGE", "localhost/sis-agent-runner:latest"), agent=e.get("SIS_AGENT", "scripted"))


def _raise_for(r: httpx.Response, what: str) -> None:
    """4xx means the request itself is wrong, so retrying cannot help; 5xx and network errors are retried by the workflow's policy."""
    if 400 <= r.status_code < 500:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ApplicationError(f"{what}: {detail}", non_retryable=True)
    r.raise_for_status()


@activity.defn(name="emit_event")
async def emit_event(event: dict) -> None:
    _raise_for(await deps().portal.post("/api/events", json=event), "portal-api")


@activity.defn(name="scan_text")
async def scan_text(text: str, source: str, team: str | None = None) -> dict:
    """The injection-guard verdict, plus the team's own blocklist: attempts people confirmed by merging a security PR are blocked
    even when the rules alone would let them through."""
    r = scan(text[:200_000], source)
    out = {"verdict": r.verdict.value, "score": r.score, "rules": [f.rule_id for f in r.findings], "fingerprint": fingerprint(text)}
    if team and out["verdict"] != "block":
        try:
            got = await deps().portal.get("/api/security/blocklist", params={"team": team})
            got.raise_for_status()
            if out["fingerprint"] in got.json()["fingerprints"]:
                out.update(verdict="block", rules=["team-blocklist", *out["rules"]])
        except httpx.HTTPError as e:  # the rules still apply; the blocklist is an extra
            activity.logger.warning("team blocklist unavailable for %s: %s", team, e)
    return out


@activity.defn(name="report_injection")
async def report_injection(incident: dict) -> None:
    """Records an attempt (portal-api keeps it, and the team's repository gets a security PR for new ones). Logged here too, so it shows
    in `make logs` without the text."""
    activity.logger.warning("prompt injection %s in %s (%s) on %s: score %s, rules %s", incident["verdict"], incident["source"], incident.get("actor") or "unknown",
                            incident["jira_key"], incident["score"], ",".join(incident["rules"]) or "-")
    _raise_for(await deps().portal.post("/api/security/incidents", json=incident), "portal-api")


@activity.defn(name="classify_quality")
async def classify_quality(title: str, description: str, kind: str = "task") -> list:
    ok, missing = classify.ticket_quality(title, description, kind)
    return [ok, missing]


@activity.defn(name="classify_risk")
async def classify_risk(title: str, description: str) -> str:
    return classify.risk_tier(title, description)


@activity.defn(name="classify_route")
async def classify_route(risk: str) -> str:
    return os.environ.get("SIS_PROFILE", "cheap-best")  # one profile for now; routing by risk comes with more models


@activity.defn(name="checkout_repo")
async def checkout_repo(repo: str, ref: str, team: str | None = None) -> dict:
    r = await deps().broker.post("/v1/checkout", json={"repo": repo, "ref": ref, "team": team})
    _raise_for(r, "git-broker")
    return r.json()


@activity.defn(name="build_context")
async def build_context(jira_key: str, title: str, description: str, team: str | None, repo: str, lesson_include: int | None = None,
                        lesson_exclude: int | None = None) -> str:
    """Background for the agent. If the context service is unavailable the task still runs, with a note saying so.
    A replay names the lesson it is testing (include or exclude) and is not a task, so nothing is recorded against it."""
    replay = lesson_include is not None or lesson_exclude is not None
    try:
        r = await deps().context.post("/v1/pack", json={"task_id": None if replay else jira_key, "jira_key": jira_key, "title": title, "description": description[:4000],
                                                        "team": team, "repo": repo, "lesson_include": lesson_include, "lesson_exclude": lesson_exclude})
        r.raise_for_status()
        return r.json()["markdown"]
    except Exception as e:
        activity.logger.warning("context pack unavailable: %s", e)
        return f"# Context for: {jira_key} {title}\n\n(No additional context was available for this task.)\n"


@activity.defn(name="start_sandbox")
async def start_sandbox(inp: TaskInput, commit: str, context_md: str, budget: float, profile: str, role: str = "developer",
                        description: str = "", documents: dict | None = None) -> None:
    """One agent in a fresh sandbox. role says which: planner, intent, architect or developer. description is the ticket plus any reviewer feedback."""
    d = deps()
    instructions = (inp.policy or {}).get("instructions", "")
    reg = await d.gateway.post("/internal/tasks", json={
        "task_id": inp.jira_key, "workflow_id": f"task-{inp.jira_key}", "namespace": inp.namespace or "default", "title": inp.title, "team": inp.team, "repo": inp.repo, "commit": commit,
        "description": wrap_untrusted(description or inp.description, "jira_ticket", inp.jira_key), "context_md": context_md, "budget_usd": budget,
        "role": role, "instructions": instructions, "documents": documents or {}})
    _raise_for(reg, "sandbox-gateway")
    token = reg.json()["token"]
    created = await d.provisioner.post("/v1/sandboxes", json={
        "task_id": inp.jira_key, "image": d.sandbox_image, "ttl_seconds": inp.max_run_seconds + 600,
        "env": {"SIS_GATEWAY_URL": d.sandbox_gateway_url, "SIS_TASK_TOKEN": token, "SIS_AGENT": d.agent, "SIS_PROFILE": profile,
                "SIS_MODEL_IMPLEMENTATION": d.settings.model_implementation}})
    if created.status_code >= 300:
        await d.gateway.delete(f"/internal/tasks/{inp.jira_key}")  # do not leave a live token behind a sandbox that never started
        _raise_for(created, "sandbox-provisioner")
        created.raise_for_status()


SANDBOX_TAIL = 200  # lines of the sandbox's own output kept when it stops


async def sandbox_output(task_id: str) -> list[str]:
    """The last lines the sandbox printed (tracebacks and tool errors the agent never reported), read before it is deleted. They go on the
    task's log (dashboard, 3D view) and come back as this activity's result, so Temporal's event history shows them too. Best effort."""
    d = deps()
    try:
        r = await d.provisioner.get(f"/v1/sandboxes/{task_id}/logs", params={"tail": SANDBOX_TAIL})
        lines = [line for line in r.json()["lines"] if line.strip()] if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError, KeyError) as e:
        activity.logger.info("could not read the output of sandbox %s: %s", task_id, e)
        return []
    ts = int(time.time() * 1000)
    for i, text in enumerate([f"Sandbox output, last {len(lines)} line(s):", *lines] if lines else []):
        try:
            await d.portal.post("/api/events", json={"type": "log.line", "taskId": task_id, "ts": ts + i, "source": "sandbox", "text": text[:2000]})
        except httpx.HTTPError:
            break  # the lines are still in this activity's result
    return lines


@activity.defn(name="stop_sandbox")
async def stop_sandbox(task_id: str) -> list[str]:
    """Idempotent. Keeps the sandbox's output, then tries both halves of the teardown even if one fails, then reports."""
    output = await sandbox_output(task_id)
    d, errors = deps(), []
    for client, path in ((d.provisioner, f"/v1/sandboxes/{task_id}"), (d.gateway, f"/internal/tasks/{task_id}")):
        try:
            r = await client.delete(path)
            if r.status_code >= 500:
                errors.append(f"{path}: {r.status_code}")
        except httpx.HTTPError as e:
            errors.append(f"{path}: {e}")
    if errors:
        raise RuntimeError("could not fully stop the sandbox: " + "; ".join(errors))
    return output


@activity.defn(name="set_budget")
async def set_budget(task_id: str, budget_usd: float) -> None:
    _raise_for(await deps().gateway.patch(f"/internal/tasks/{task_id}/budget", json={"budget_usd": budget_usd}), "sandbox-gateway")


@activity.defn(name="publish_pr")
async def publish_pr(repo: str, commit: str, branch: str, patch: str, title: str, body: str, base_branch: str, jira_key: str, team: str | None = None) -> dict:
    r = await deps().broker.post("/v1/publish", json={"repo": repo, "base_commit": commit, "branch": branch, "patch": patch, "title": title[:200], "body": body,
                                                      "base_branch": base_branch, "jira_key": jira_key, "team": team})
    _raise_for(r, "git-broker")
    return r.json()


@activity.defn(name="pr_status")
async def pr_status(repo: str, number: int, team: str | None, mention: str) -> dict:
    r = await deps().broker.get(f"/v1/pr/{repo}/{number}", params={"mention": mention, **({"team": team} if team else {})})
    _raise_for(r, "git-broker")
    return r.json()


@activity.defn(name="update_pr")
async def update_pr(repo: str, commit: str, branch: str, patch: str, message: str, number: int, comment: str, team: str | None = None) -> dict:
    r = await deps().broker.post("/v1/update", json={"repo": repo, "base_commit": commit, "branch": branch, "patch": patch, "message": message[:2000],
                                                     "pr_number": number, "comment": comment, "team": team})
    _raise_for(r, "git-broker")
    return r.json()


@activity.defn(name="pr_comment")
async def pr_comment(repo: str, number: int, body: str, team: str | None = None) -> None:
    r = await deps().broker.post("/v1/comment", json={"repo": repo, "pr_number": number, "body": body, "team": team})
    _raise_for(r, "git-broker")


@activity.defn(name="record_eval_case")
async def record_eval_case(repo: str, base_commit: str, head: str, team: str | None, task_id: str, title: str, description: str) -> None:
    """A merged ticket becomes a replay case: what was asked, where the work started, and the diff people finally merged."""
    d = deps()
    r = await d.broker.post("/v1/diff", json={"repo": repo, "base": base_commit, "head": head, "team": team})
    _raise_for(r, "git-broker")
    _raise_for(await d.portal.post("/api/eval/cases", json={"task_id": task_id, "repo": repo, "team": team, "base_commit": base_commit, "title": title,
                                                             "description": description, "reference": r.json()["diff"]}), "portal-api")


@activity.defn(name="report_eval")
async def report_eval(run_id: int, result: dict) -> None:
    _raise_for(await deps().portal.post(f"/api/eval/runs/{run_id}/result", json=result), "portal-api")


ALL = [emit_event, scan_text, report_injection, classify_quality, classify_risk, classify_route, checkout_repo, build_context, start_sandbox, stop_sandbox, set_budget, publish_pr,
       pr_status, update_pr, pr_comment, record_eval_case, report_eval]
