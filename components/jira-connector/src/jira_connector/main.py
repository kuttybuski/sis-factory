import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from temporalio.worker import Worker

from sif_contracts import posture
from sif_contracts import Settings
from sif_contracts.connections import Credentials
from sif_contracts.namespaces import NamespaceWorkers, Namespaces, namespaces_of, team_namespaces
from sif_contracts.workflow import JIRA_QUEUE

from jira_connector import activities
from jira_connector.client import DryRunJira, JiraClient
from jira_connector.flow import TemporalFlow
from jira_connector.router import JiraRouter, NoConnection, Scope
from jira_connector.poller import Config, Seen, pickup_cycle, relay_answer, reply_cycle, team_for

log = logging.getLogger("jira_connector")
logging.basicConfig(level=logging.INFO)


async def teams_of(portal: httpx.AsyncClient) -> list[dict] | None:
    """The teams, or None if portal-api cannot be reached."""
    try:
        r = await portal.get("/api/teams")
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def effective_policy(portal: httpx.AsyncClient, team: str | None, kind: str) -> dict | None:
    """The handling policy for a ticket, from portal-api. None if it cannot be had: the ticket then gets the built-in policy rather than waiting."""
    try:
        r = await portal.get("/api/handling/effective", params={"kind": kind, **({"team": team} if team else {})})
        return r.json()["policy"] if r.status_code == 200 else None
    except (httpx.HTTPError, KeyError, ValueError):
        log.warning("could not fetch the handling policy for %s; using the built-in one", kind)
        return None


async def loop(name: str, every: float, fn, state: dict) -> None:
    while True:
        try:
            await fn()
            state[name] = "ok"
        except Exception as e:
            state[name] = f"{type(e).__name__}: {e}"[:200]
            log.warning("%s failed: %s", name, state[name])
        await asyncio.sleep(every)


@asynccontextmanager
async def lifespan(app: FastAPI):
    posture.enforce("jira-connector")  # SIF_ENV=production: refuse to start with unsafe settings (sif_contracts.posture)
    env = dict(os.environ)
    dry = env.get("SIF_DRY_RUN", "false").lower() in ("1", "true", "yes")
    wrap = (lambda c: DryRunJira(c, log.info)) if dry else (lambda c: c)
    # The shared account from the environment covers every project that no team has its own connection for.
    shared = wrap(JiraClient(env["JIRA_BASE_URL"], env.get("JIRA_EMAIL", ""), env.get("JIRA_API_TOKEN", ""))) if env.get("JIRA_BASE_URL") else None
    portal_url = env.get("PORTAL_API_URL", "http://portal-api:8000")
    credentials = Credentials(portal_url, env["SIF_INTERNAL_TOKEN"], ttl=Settings().connection_cache_seconds) if env.get("SIF_INTERNAL_TOKEN") else None
    router = JiraRouter(shared=shared, credentials=credentials, wrap=wrap)
    cfg = Config.from_env(env)
    portal = httpx.AsyncClient(base_url=portal_url, headers={"Authorization": f"Bearer {env.get('SIF_INGEST_TOKEN', '')}"}, timeout=15)
    activities.set_client(router)
    s = Settings()
    namespaces = Namespaces(env.get("TEMPORAL_ADDRESS", "temporal:7233"), s.namespace_retention_days)
    flow, seen, state = TemporalFlow(namespaces), Seen(), {}
    every = float(env.get("SIF_POLL_SECONDS", "30"))
    # Workflows call the Jira activities on JIRA_QUEUE in their own namespace, so a worker runs in every team namespace.
    workers = NamespaceWorkers(namespaces, lambda c: Worker(c, task_queue=JIRA_QUEUE, activities=activities.ALL), lambda: team_namespaces(portal),
                               every=s.namespace_refresh_seconds)
    await workers.sync()
    app.state.workers = workers
    app.state.tasks = [asyncio.create_task(workers.run()),
                       asyncio.create_task(loop("pickup", every, lambda: _pickup(router, flow, portal, cfg, dry), state)),
                       asyncio.create_task(loop("replies", every, lambda: _replies(router, flow, portal, cfg, seen), state))]
    app.state.status, app.state.dry, app.state.router = state, dry, router
    app.state.flow, app.state.seen, app.state.portal = flow, seen, portal
    yield
    for t in app.state.tasks:
        t.cancel()
    await workers.stop()



async def _scopes(router: JiraRouter, portal, cfg) -> tuple[list[dict], list[Scope]]:
    """The teams and the Jira accounts to search this cycle. If the portal cannot say who has their own connection the cycle is skipped:
    searching with the shared account could pick up a team's tickets as the wrong identity."""
    teams = await teams_of(portal)
    if teams is None:
        if router.credentials is not None:
            raise RuntimeError("portal-api is not reachable, so the teams' Jira connections are unknown")
        teams = []
    await router.refresh(teams)
    return teams, await router.scopes(cfg.bot_account_id)


async def _pickup(router, flow, portal, cfg, dry):
    teams, scopes = await _scopes(router, portal, cfg)
    flow.known = namespaces_of(teams)
    r = await portal.get("/api/intake")  # pauses and per-team counts: if the portal cannot say, nothing starts
    r.raise_for_status()
    intake = r.json()
    for sc in scopes:
        if started := await pickup_cycle(sc.client, flow, teams, cfg, dry_run=dry, scope=sc.jql, intake=intake,
                                         policy_for=lambda team, kind: effective_policy(portal, team, kind)):
            log.info("started (%s): %s", sc.name, started)


async def _replies(router, flow, portal, cfg, seen):
    teams, scopes = await _scopes(router, portal, cfg)
    for sc in scopes:
        await reply_cycle(sc.client, flow, portal, teams, replace(cfg, bot_account_id=sc.bot_account_id or cfg.bot_account_id), seen, scope=sc.jql)


app = FastAPI(title="sif jira-connector", lifespan=lifespan)


def internal(authorization: str | None = Header(default=None)) -> None:
    token = os.environ.get("SIF_INTERNAL_TOKEN", "")
    if not token:
        raise HTTPException(503, "SIF_INTERNAL_TOKEN is not configured")
    if not secrets.compare_digest((authorization or "").removeprefix("Bearer "), token):
        raise HTTPException(401, "invalid token")


class AnswerIn(BaseModel):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9_]*-\d+$")
    text: str = Field(min_length=1, max_length=4000)
    by: str = Field(min_length=1, max_length=200)


@app.post("/v1/answer", dependencies=[Depends(internal)])
async def answer(body: AnswerIn):
    """portal-api passes on an answer a person typed on the 3D view (portal-web checked that they may answer for the task's team)."""
    teams = await teams_of(app.state.portal) or []
    await app.state.router.refresh(teams)
    try:
        delivered = await relay_answer(app.state.router, app.state.flow, team_for(body.key, teams), body.key, body.text.strip(), body.by, app.state.seen)
    except NoConnection as e:
        raise HTTPException(409, str(e)) from None
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Jira refused the comment ({e.response.status_code})") from None
    if not delivered:
        raise HTTPException(409, f"the answer is on {body.key} in Jira, but the ticket has no running task to give it to")
    return {"delivered": True, "dry_run": app.state.dry}


@app.get("/healthz")
def healthz():
    r = app.state.router
    return {"ok": not any(t.done() for t in app.state.tasks) and app.state.workers.healthy(), "namespaces": app.state.workers.status(), "dry_run": app.state.dry, "shared_account": r.shared is not None, "teams": r.status, **app.state.status}
