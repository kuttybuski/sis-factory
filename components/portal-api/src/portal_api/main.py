import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from sis_contracts import posture
from sis_contracts.events import FactoryEvent

from portal_api import connections, db, handling, intake, knowledge, learning, security, teams, vault, workflows
from portal_api.auth import dashboard_guard, pool, write_guard


class Hub:
    """In-process fan-out to connected SSE clients. Single replica for now; use Postgres LISTEN/NOTIFY to scale out."""

    def __init__(self) -> None:
        self.clients: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(self, payload: dict[str, Any]) -> None:
        for q in self.clients:
            q.put_nowait(payload)


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    posture.enforce("portal-api")  # SIS_ENV=production: refuse to start with unsafe settings (sis_contracts.posture)
    app.state.pool = pool = await db.open_pool(os.environ["DATABASE_URL"])
    if moved := await vault.reencrypt(pool):
        print(f"re-encrypted {moved} connection secret(s) with the current SIS_SECRET_KEY", flush=True)
    loops = [asyncio.create_task(learning.improve_forever(pool)), asyncio.create_task(purge_forever(pool))]
    yield
    for loop in loops:
        loop.cancel()
    await pool.close()


async def purge_forever(p) -> None:
    """Retention, once a day: event payloads of finished tasks after SIS_RETENTION_DAYS, audit rows after SIS_AUDIT_RETENTION_DAYS (0 = keep)."""
    event_days, audit_days = int(os.environ.get("SIS_RETENTION_DAYS") or 0), int(os.environ.get("SIS_AUDIT_RETENTION_DAYS") or 0)  # unset: keep
    if event_days <= 0 and audit_days <= 0:
        return
    while True:
        try:
            n = await db.purge(p, event_days=event_days, audit_days=audit_days)
            if any(n.values()):
                print(f"retention: deleted {n['events']} event(s) and {n['audit']} audit row(s)", flush=True)
        except Exception as e:  # noqa: BLE001 - try again tomorrow
            print(f"retention failed: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(86400)


app = FastAPI(title="sis portal-api", lifespan=lifespan)
app.include_router(connections.router)
app.include_router(teams.router)
app.include_router(knowledge.router)
app.include_router(intake.router)
app.include_router(handling.router)
app.include_router(learning.router)
app.include_router(security.router)
app.include_router(workflows.router)

# Service traffic that is not a decision by anyone: the event stream, the orchestrator acknowledging kills, and its polls for replay runs
# and due knowledge PRs (every few seconds; the request behind a replay is audited when someone makes it, and knowledge PRs are kept in digests).
UNAUDITED = ("/api/events", "/api/kill-requests/handled", "/api/eval/runs/claim", "/api/digests/claim", "/api/security/claim")


@app.middleware("http")
async def audit(request: Request, call_next):
    """Every change made through the API, by whom, and whether it was allowed. The dashboard sends the signed-in user as X-Sis-Actor;
    services send nothing and are recorded as 'service'. Refused attempts (401, 403, 409, ...) are recorded too."""
    response = await call_next(request)
    path = request.url.path
    if request.method not in ("GET", "HEAD", "OPTIONS") and path.startswith("/api/") and path not in UNAUDITED:
        actor = (request.headers.get("x-sis-actor") or "service")[:200]
        # Also one JSON line on stdout, for the log shipper to forward to the SIEM: a copy outside the database it describes.
        print(json.dumps({"audit": {"at": datetime.now(UTC).isoformat(), "actor": actor, "method": request.method, "path": path[:500],
                                    "status": response.status_code}}), flush=True)
        try:
            await db.record_audit(request.app.state.pool, actor=actor, method=request.method, path=path[:500], status=response.status_code)
        except Exception:  # noqa: BLE001 - the change already happened; a lost audit row must not turn it into an error
            pass
    return response


@app.get("/api/audit")
async def audit_log(limit: int = Query(200, ge=1, le=1000), actor: str | None = None, p=Depends(pool)):
    return await db.list_audit(p, limit=limit, actor=actor)


@app.get("/api/healthz")
async def healthz(p=Depends(pool)):
    async with p.connection() as conn:
        await conn.execute("select 1")
    return {"ok": True}


@app.post("/api/events", status_code=202, dependencies=[Depends(write_guard)])
async def post_event(ev: FactoryEvent, p=Depends(pool)):
    applied = await db.ingest(p, ev)
    if applied:
        hub.publish(ev.model_dump(by_alias=True, exclude_none=True, mode="json"))
    return {"applied": applied}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@app.get("/api/events")
async def stream_events(request: Request, p=Depends(pool)):
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    hub.clients.add(q)  # subscribe before replaying so nothing is missed (a few duplicates are possible)

    async def gen():
        try:
            yield ": connected\n\n"
            for payload in await db.replay(p):
                yield _sse(payload)
            yield "event: replayed\ndata: {}\n\n"  # the view swaps in the replayed tasks at once instead of adding them to what it shows
            while not await request.is_disconnected():
                try:
                    yield _sse(await asyncio.wait_for(q.get(), 15))
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            hub.clients.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/overview")
async def overview(team: str | None = None, include_demo: bool = False, p=Depends(pool)):
    return await db.overview(p, include_demo=include_demo, team=team)


@app.get("/api/tasks")
async def tasks(team: str | None = None, outcome: str | None = None, q: str | None = None, days: int | None = Query(None, ge=1, le=3650),
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), include_demo: bool = False, p=Depends(pool)):
    return await db.list_tasks(p, team=team, outcome=outcome, q=q, days=days, limit=limit, offset=offset, include_demo=include_demo)


@app.get("/api/scorecard")
async def scorecard(days: int = Query(30, ge=1, le=3650), team: str | None = None, include_demo: bool = False, p=Depends(pool)):
    return await db.scorecard(p, days=days, include_demo=include_demo, team=team)


@app.get("/api/tasks/{task_id}")
async def task(task_id: str, p=Depends(pool)):
    found = await db.get_task(p, task_id)
    if found is None:
        raise HTTPException(404, "task not found")
    return found


class KillRequest(BaseModel):
    requested_by: str = "dashboard"


@app.post("/api/tasks/{task_id}/kill", status_code=202, dependencies=[Depends(dashboard_guard)])
async def kill(task_id: str, body: KillRequest | None = None, p=Depends(pool)):
    # The orchestrator acts on the request (cancel workflow, delete sandbox); the live view updates from its task.killed event.
    status = await db.request_kill(p, task_id, (body or KillRequest()).requested_by)
    if status == "unknown":
        raise HTTPException(404, "task not found")
    if status == "finished":
        raise HTTPException(409, "task already finished")
    return {"status": status}


class AnswerIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    by: str = Field(min_length=1, max_length=200)


@app.post("/api/tasks/{task_id}/answer", dependencies=[Depends(dashboard_guard)])
async def answer(task_id: str, body: AnswerIn, p=Depends(pool)):
    """An answer typed on the 3D view. jira-connector posts it on the ticket (Jira stays the record) and then gives it to the waiting task."""
    found = await db.get_task(p, task_id)
    if found is None:
        raise HTTPException(404, "task not found")
    if found["task"]["stage"] != "asking":
        raise HTTPException(409, "the task is not waiting for a reply")
    url, token = os.environ.get("JIRA_CONNECTOR_URL", "http://jira-connector:8080"), os.environ.get("SIS_INTERNAL_TOKEN", "")
    try:
        async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}"}) as c:
            r = await c.post(f"{url}/v1/answer", json={"key": found["task"]["jira_key"], "text": body.text, "by": body.by})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"jira-connector is not reachable ({type(e).__name__})") from None
    if r.status_code != 200:
        detail = r.json().get("detail") if "json" in r.headers.get("content-type", "") else r.text[:200]
        raise HTTPException(409 if r.status_code == 409 else 502, detail)
    return r.json()


@app.get("/api/costs")
async def costs(group_by: str = "team", days: int = Query(30, ge=1, le=3650), team: str | None = None, include_demo: bool = False, p=Depends(pool)):
    if group_by not in db.GROUPS:
        raise HTTPException(422, f"group_by must be one of {sorted(db.GROUPS)}")
    return await db.costs(p, group_by=group_by, days=days, include_demo=include_demo, team=team)


# ---------------------------------------------------------------- kill requests, consumed by the orchestrator
@app.get("/api/kill-requests/pending", dependencies=[Depends(write_guard)])
async def pending_kill_requests(p=Depends(pool)):
    return await db.pending_kills(p)


class HandledIn(BaseModel):
    ids: list[int]


@app.post("/api/kill-requests/handled", dependencies=[Depends(write_guard)])
async def kill_requests_handled(body: HandledIn, p=Depends(pool)):
    return {"handled": await db.mark_kills_handled(p, body.ids)}
