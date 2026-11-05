import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Query, Request
from pydantic import BaseModel, Field

from sif_context import db
from sif_context.embed import embedder_from_env
from sif_context.pack import build_pack
from sif_contracts import posture

log = logging.getLogger("context_api")
PORTAL_API_URL = os.environ.get("PORTAL_API_URL", "http://portal-api:8000")
GAP_SIM = float(os.environ.get("SIF_CONTEXT_GAP_SIM", "0.3"))  # below this best similarity the task's topic counts as undocumented
QA_REUSE_SIM = float(os.environ.get("SIF_QA_REUSE_SIM", "0.8"))  # an earlier answer this similar may be offered instead of asking again
QA_PACK_SIM = float(os.environ.get("SIF_QA_PACK_SIM", "0.5"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    posture.enforce("context-api")  # SIF_ENV=production: refuse to start with unsafe settings; also loads NAME_FILE secrets
    pool = await db.open_pool(os.environ["DATABASE_URL"])
    app.state.pool, app.state.embedder = pool, embedder_from_env()
    app.state.portal = httpx.AsyncClient(base_url=PORTAL_API_URL, timeout=10)
    yield
    await app.state.portal.aclose()
    await pool.close()


app = FastAPI(title="sif context-api", lifespan=lifespan)
pool = lambda r: r.app.state.pool  # noqa: E731


class SearchRequest(BaseModel):
    query: str = Field(max_length=4000)
    team: str | None = None
    repo: str | None = None
    paths: list[str] = []
    kinds: list[str] = []
    k: int = Field(8, ge=1, le=30)


@app.get("/healthz")
async def healthz(request: Request):
    async with pool(request).connection() as c:
        await c.execute("select 1")
    return {"ok": True, "embedding_model": request.app.state.embedder.model}


@app.post("/v1/search")
async def search(req: SearchRequest, request: Request):
    hits = await db.search(pool(request), request.app.state.embedder, req.query, db.SearchCtx(team=req.team, repo=req.repo, paths=req.paths, kinds=req.kinds), k=req.k)
    return {"hits": [h.model_dump() for h in hits]}


async def fetch_rules(request: Request, team, repo, paths, query, task_id, include: int | None = None, exclude: int | None = None) -> tuple[str, bool]:
    """Approved team lessons from portal-api. A failure must not stop a task, so it degrades to no rules and says so.
    include / exclude: a replay testing one lesson forces it in or out."""
    extra = {k: v for k, v in (("task_id", task_id), ("include", include), ("exclude", exclude)) if v is not None}
    try:
        r = await request.app.state.portal.get("/api/lessons/relevant", params={
            "repo": repo, "team": team, "paths": ",".join(paths), "q": query[:3000], "record_use": "true" if task_id else "false", **extra})
        r.raise_for_status()
        body = r.json()
        return "\n".join(x for x in (body.get("knowledge", ""), body["rules"]) if x), True  # the team's own page first, then the lessons
    except Exception as e:
        log.warning("could not fetch lessons: %s", e)
        return "", False


class PackRequest(BaseModel):
    task_id: str | None = None
    jira_key: str = ""
    title: str
    description: str = ""
    team: str | None = None
    repo: str | None = None
    paths: list[str] = []
    budget_tokens: int = Field(6000, ge=500, le=40000)
    include_flagged: bool = False
    lesson_include: int | None = None  # replays: the lesson under test, delivered even if it is still a candidate
    lesson_exclude: int | None = None  # replays: the same lesson withheld, for the comparison arm


@app.post("/v1/pack")
async def make_pack(req: PackRequest, request: Request):
    """Everything an agent should know before it starts: trusted team rules, earlier answers, similar PRs and documentation."""
    p, emb = pool(request), request.app.state.embedder
    query = f"{req.title}\n{req.description}"[:2000]
    ctx = db.SearchCtx(team=req.team, repo=req.repo, paths=req.paths)
    rules, lessons_ok = await fetch_rules(request, req.team, req.repo, req.paths, query, req.task_id, req.lesson_include, req.lesson_exclude)
    qa, prs, docs = await asyncio.gather(
        db.search(p, emb, query, ctx.model_copy(update={"kinds": ["qa"]}), k=3),
        db.search(p, emb, query, ctx.model_copy(update={"kinds": ["pr"]}), k=5),
        db.search(p, emb, query, ctx.model_copy(update={"kinds": ["page", "doc", "note"]}), k=8))
    qa = [h for h in qa if h.sim >= QA_PACK_SIM]
    pack = build_pack(title=f"{req.jira_key} {req.title}".strip(), rules_block=rules, qa=qa, prs=prs, docs=docs,
                      budget_tokens=req.budget_tokens, include_flagged=req.include_flagged)
    if req.task_id:
        for section, hits in pack.sections.items():
            await db.record_usage(p, req.task_id, section, hits)
    if not docs or max(h.sim for h in docs) < GAP_SIM:
        await db.record_gap(p, task_id=req.task_id, query=req.title, best_score=max((h.sim for h in docs), default=None), repo=req.repo, team=req.team)
    return {"markdown": pack.markdown, "citations": [c.model_dump() for c in pack.citations], "best_score": pack.best_score,
            "rules_included": bool(rules), "lessons_ok": lessons_ok}


class SimilarRequest(BaseModel):
    question: str = Field(max_length=2000)
    team: str | None = None
    repo: str | None = None
    threshold: float | None = None


_QA = re.compile(r"Question:\s*(.*?)\nAnswer:\s*(.*)", re.DOTALL)


@app.post("/v1/similar-questions")
async def similar_questions(req: SimilarRequest, request: Request):
    """Called by sandbox-gateway before a question goes to Jira. A close match lets the agent reuse an earlier human answer."""
    hits = await db.search(pool(request), request.app.state.embedder, req.question, db.SearchCtx(team=req.team, repo=req.repo, kinds=["qa"]), k=3)
    out = []
    for h in hits:
        m = _QA.match(h.text)
        if m and not h.flagged and h.sim >= (req.threshold if req.threshold is not None else QA_REUSE_SIM):
            out.append({"question": m.group(1).strip(), "answer": m.group(2).strip(), "similarity": round(h.sim, 3), "task": h.meta.get("jira_key", "")})
    return {"matches": out}


@app.get("/v1/sources")
async def sources(request: Request):
    return await db.list_sources(pool(request))


@app.get("/v1/quarantine")
async def quarantine(request: Request, limit: int = Query(50, le=200)):
    return await db.list_quarantine(pool(request), limit)


@app.get("/v1/gaps")
async def gaps(request: Request, limit: int = Query(50, le=200)):
    return await db.list_gaps(pool(request), limit)


@app.get("/v1/usage/{task_id}")
async def usage(task_id: str, request: Request):
    return await db.task_usage(pool(request), task_id)
