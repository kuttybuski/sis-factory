import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from sis_context import db
from sis_context.embed import embedder_from_env
from sis_context.models import DocIn
from sis_context.text import md_to_chunks
from sis_contracts import posture
from sis_contracts import Settings
from sis_contracts.connections import Credentials

from context_ingest import sync

log = logging.getLogger("context_ingest")
SYNC_MINUTES = int(os.environ.get("SIS_CONTEXT_SYNC_MINUTES", "60"))  # 0 disables the built-in schedule


async def teams(pool):
    async with pool.connection() as c:
        try:
            return await (await c.execute("select name, confluence_spaces, repos from teams")).fetchall()
        except Exception:  # portal-api has not migrated yet
            return []


async def sync_all(app: FastAPI) -> list[dict]:
    pool = app.state.pool
    await sync.reconcile(pool, await teams(pool))
    out = []
    for s in await db.list_sources(pool):
        if s["enabled"] and s["kind"] != "push":
            async with app.state.locks.setdefault(s["id"], asyncio.Lock()):  # never two syncs of one source at once
                out.append(await sync.sync_one(pool, s, app.state.embedder, app.state.cfg))
    return out


async def scheduler(app: FastAPI):
    while True:
        await asyncio.sleep(SYNC_MINUTES * 60)
        try:
            await sync_all(app)
        except Exception:
            log.exception("scheduled sync failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    posture.enforce("context-ingest")  # SIS_ENV=production: refuse to start with unsafe settings (sis_contracts.posture)
    pool = await db.open_pool(os.environ["DATABASE_URL"])
    cfg = sync.Config()
    internal = os.environ.get("SIS_INTERNAL_TOKEN", "")
    if cfg.portal_url and internal:  # without it every team uses the shared credentials
        cfg.credentials = Credentials(cfg.portal_url, internal, ttl=Settings().connection_cache_seconds)
    app.state.pool, app.state.embedder, app.state.cfg, app.state.locks = pool, embedder_from_env(), cfg, {}
    task = asyncio.create_task(scheduler(app)) if SYNC_MINUTES > 0 else None
    yield
    if task:
        task.cancel()
    await pool.close()


app = FastAPI(title="sis context-ingest", lifespan=lifespan)


def require_token(authorization: str | None = Header(default=None)) -> None:
    token = os.environ.get("SIS_INGEST_TOKEN", "")
    if not token:
        raise HTTPException(503, "SIS_INGEST_TOKEN is not configured")
    if not secrets.compare_digest((authorization or "").removeprefix("Bearer "), token):
        raise HTTPException(401, "invalid token")


@app.get("/healthz")
async def healthz(request: Request):
    async with request.app.state.pool.connection() as c:
        await c.execute("select 1")
    return {"ok": True, "embedding_model": request.app.state.embedder.model}


@app.post("/v1/reconcile", dependencies=[Depends(require_token)])
async def reconcile(request: Request):
    pool = request.app.state.pool
    return await sync.reconcile(pool, await teams(pool))


class SyncRequest(BaseModel):
    source_id: int | None = None  # omit to sync everything


@app.post("/v1/sync", status_code=202, dependencies=[Depends(require_token)])
async def start_sync(body: SyncRequest, request: Request, background: BackgroundTasks):
    app_ = request.app
    if body.source_id is None:
        background.add_task(sync_all, app_)
        return {"started": "all"}
    source = await db.get_source(app_.state.pool, body.source_id)
    if source is None:
        raise HTTPException(404, "source not found")

    async def one():
        async with app_.state.locks.setdefault(source["id"], asyncio.Lock()):
            await sync.sync_one(app_.state.pool, source, app_.state.embedder, app_.state.cfg)

    background.add_task(one)
    return {"started": source["id"]}


class PushDoc(BaseModel):
    external_id: str
    title: str
    text: str  # markdown or plain text
    url: str = ""
    team: str | None = None
    repo: str | None = None
    version: str = "1"


class PushRequest(BaseModel):
    source: str  # your name for this feed
    docs: list[PushDoc]
    team: str | None = None
    repo: str | None = None
    replace: bool = False  # true: documents missing from this push are removed


@app.post("/v1/ingest/docs", dependencies=[Depends(require_token)])
async def ingest_docs(body: PushRequest, request: Request):
    """Lets any system feed the index (runbooks, incident reports, exported tickets) without a new connector."""
    pool = request.app.state.pool
    src = await db.upsert_source(pool, kind="push", key=body.source, team=body.team, repo=body.repo)

    async def docs():
        for d in body.docs:
            yield DocIn(kind="note", external_id=d.external_id, title=d.title, url=d.url, version=d.version, team=d.team or body.team,
                        repo=d.repo or body.repo, chunks=md_to_chunks(d.text, d.title, body.source))

    stats = await db.sync_docs(pool, src["id"], docs(), request.app.state.embedder, complete=body.replace)
    await db.record_sync(pool, src["id"], ok=True, error=None, stats=stats)
    return {"source_id": src["id"], **stats.model_dump()}


@app.delete("/v1/sources/{source_id}", dependencies=[Depends(require_token)])
async def delete_source(source_id: int, request: Request):
    if not await db.delete_source(request.app.state.pool, source_id):
        raise HTTPException(404, "source not found")
    return {"deleted": source_id}
