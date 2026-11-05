import json
import logging
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from injection_guard import SYSTEM_GUIDANCE, Verdict, check_outbound, redact, scan
from sis_contracts import posture
from sis_contracts import handling as H
from sis_contracts.workflow import Q_ANSWER, SIG_COST, SIG_DONE, SIG_FAILED, SIG_LOG, SIG_QUESTION, AgentResult, Question

from sandbox_gateway.orchestrator import TemporalOrchestrator
from sandbox_gateway.state import Registry, TaskRec, cost_of, load_prices

log = logging.getLogger("sandbox_gateway")
MAX_RESULT_BYTES = 1_000_000


class Config:
    """Read when the app starts, never at import time."""

    def __init__(self) -> None:
        e = os.environ
        self.internal_token = e.get("SIS_INTERNAL_TOKEN", "")
        self.ingest_token = e.get("SIS_INGEST_TOKEN", "")
        self.llm_url = e.get("LLM_UPSTREAM_URL", "").rstrip("/")
        self.llm_key = e.get("LLM_UPSTREAM_KEY", "")
        self.llm_style = e.get("LLM_UPSTREAM_AUTH", "bearer")  # bearer | x-api-key
        self.models = {m for m in e.get("SIS_ALLOWED_MODELS", "").split(",") if m}
        self.prices = load_prices(e.get("SIS_PRICE_TABLE"))
        self.max_asks = int(e.get("SIS_MAX_ASKS", "10"))
        self.allowed_hosts = {h for h in e.get("SIS_ALLOWED_LINK_HOSTS", "github.com").split(",") if h}
        self.git_broker = e.get("GIT_BROKER_URL", "http://git-broker:8080")
        self.context_api = e.get("CONTEXT_API_URL", "http://context-api:8080")
        self.portal_api = e.get("PORTAL_API_URL", "http://portal-api:8000")
        self.qa_reuse = float(e.get("SIS_QA_REUSE_SIM", "0.8"))

    @property
    def secret_literals(self) -> list[str]:
        return [s for s in (self.llm_key, self.internal_token, self.ingest_token) if len(s) >= 8]


@asynccontextmanager
async def lifespan(app: FastAPI):
    posture.enforce("sandbox-gateway")  # SIS_ENV=production: refuse to start with unsafe settings (sis_contracts.posture)
    app.state.cfg = Config()
    app.state.registry = Registry()
    if getattr(app.state, "orch", None) is None:
        app.state.orch = TemporalOrchestrator(os.environ.get("TEMPORAL_ADDRESS", "temporal:7233"))
    app.state.http = getattr(app.state, "http", None) or httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10))
    yield
    await app.state.http.aclose()


app = FastAPI(title="sis sandbox-gateway", lifespan=lifespan)


def internal(request: Request, authorization: str | None = Header(default=None)) -> None:
    token = request.app.state.cfg.internal_token
    if not token:
        raise HTTPException(503, "SIS_INTERNAL_TOKEN is not configured")
    if not secrets.compare_digest((authorization or "").removeprefix("Bearer "), token):
        raise HTTPException(401, "invalid token")


def task_of(request: Request, authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)) -> TaskRec:
    """The sandbox authenticates with its per-task token, as a Bearer token or (for Anthropic clients) x-api-key."""
    token = (authorization or "").removeprefix("Bearer ") or (x_api_key or "")
    rec = request.app.state.registry.lookup(token) if token else None
    if rec is None or rec.finished:
        raise HTTPException(401, "unknown or finished task")
    return rec


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ------------------------------------------------------------------ orchestrator-facing
class TaskIn(BaseModel):
    task_id: str
    workflow_id: str
    namespace: str = Field("default", pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")  # the team's Temporal namespace, where the workflow runs
    title: str
    description: str = ""
    context_md: str = ""
    repo: str = ""
    commit: str = ""
    team: str | None = None
    budget_usd: float = Field(gt=0)
    role: Literal["planner", "intent", "architect", "developer"] = "developer"
    instructions: str = Field("", max_length=20000)  # the factory or team handling text; it was screened when it was saved
    documents: dict[str, str] = Field(default_factory=dict, max_length=8)


@app.post("/internal/tasks", dependencies=[Depends(internal)])
async def register_task(body: TaskIn, request: Request):
    rec = TaskRec(**body.model_dump(exclude={"instructions"}))
    rec.guidance = SYSTEM_GUIDANCE + "\n\n" + H.guidance_for(body.role, body.instructions)
    token = request.app.state.registry.register(rec)
    return {"token": token, "canary": rec.canary}


class BudgetIn(BaseModel):
    budget_usd: float = Field(gt=0)


@app.patch("/internal/tasks/{task_id}/budget", dependencies=[Depends(internal)])
async def set_budget(task_id: str, body: BudgetIn, request: Request):
    rec = request.app.state.registry.get(task_id)
    if rec is None:
        raise HTTPException(404, "unknown task")
    rec.budget_usd = body.budget_usd
    if rec.spent_usd < rec.budget_usd:
        rec.budget_ask_id = None  # allow a new budget question if the new limit is reached too
    return {"budget_usd": rec.budget_usd, "spent_usd": rec.spent_usd}


@app.get("/internal/tasks/{task_id}", dependencies=[Depends(internal)])
async def task_state(task_id: str, request: Request):
    rec = request.app.state.registry.get(task_id)
    if rec is None:
        raise HTTPException(404, "unknown task")
    return {"budget_usd": rec.budget_usd, "spent_usd": round(rec.spent_usd, 6), "estimated": rec.estimated, "asks": rec.asks}


@app.delete("/internal/tasks/{task_id}", dependencies=[Depends(internal)])
async def end_task(task_id: str, request: Request):
    rec = request.app.state.registry.get(task_id)
    if rec:
        rec.finished = True
        request.app.state.registry.remove(task_id)
    return {"removed": rec is not None}


# ------------------------------------------------------------------ sandbox-facing
@app.get("/v1/task")
async def get_task(rec: TaskRec = Depends(task_of)):
    return {"task_id": rec.task_id, "title": rec.title, "description": rec.description, "context_md": rec.context_md, "repo": rec.repo,
            "commit": rec.commit, "guidance": rec.guidance, "canary": rec.canary, "role": rec.role, "documents": rec.documents,
            "output_file": H.OUTPUT_FILE.get(rec.role, ""), "docs_dir": H.docs_dir(rec.task_id)}


@app.get("/v1/archive")
async def archive(request: Request, rec: TaskRec = Depends(task_of)):
    """The repository at the task's commit. The sandbox never sees git credentials or the broker's address."""
    cfg = request.app.state.cfg
    r = await request.app.state.http.get(f"{cfg.git_broker}/v1/archive/{rec.repo}/{rec.commit}.tar.gz", headers={"Authorization": f"Bearer {cfg.internal_token}"})
    if r.status_code != 200:
        raise HTTPException(502, "could not fetch the repository")
    return Response(r.content, media_type="application/gzip")


def screen_outbound(cfg: Config, rec: TaskRec, *texts: str) -> None:
    for t in texts:
        res = check_outbound(t, allowed_hosts=cfg.allowed_hosts, canaries=[rec.canary], literals=cfg.secret_literals)
        if not res.ok:
            log.warning("blocked outbound text for %s: %s", rec.task_id, [f.rule_id for f in res.findings])
            raise HTTPException(422, "blocked: the text looks like it contains a secret, the system prompt, or an exfiltration link")
        if scan(t, "tool_output").verdict is Verdict.BLOCK:
            raise HTTPException(422, "blocked: the text looks like a prompt-injection attempt")


class AskIn(BaseModel):
    question: str = Field(min_length=5, max_length=2000)


@app.post("/v1/ask")
async def ask(body: AskIn, request: Request, rec: TaskRec = Depends(task_of)):
    cfg, st = request.app.state.cfg, request.app.state
    screen_outbound(cfg, rec, body.question)
    try:  # an earlier human answer to a similar question saves a Jira round trip
        r = await st.http.post(f"{cfg.context_api}/v1/similar-questions", json={"question": body.question, "team": rec.team, "repo": rec.repo, "threshold": cfg.qa_reuse}, timeout=10)
        if r.status_code == 200 and (matches := r.json().get("matches")):
            m = matches[0]
            return {"id": "reused", "status": "answered", "text": m["answer"], "source": f"earlier answer on {m.get('task') or 'a previous task'}"}
    except Exception as e:
        log.info("similar-question lookup skipped: %s", e)
    if rec.asks >= cfg.max_asks:
        raise HTTPException(429, f"this task has asked {cfg.max_asks} questions already; finish with what you have or report that you are blocked")
    rec.asks += 1
    qid = uuid.uuid4().hex[:12]
    await st.orch.signal(rec.workflow_id, SIG_QUESTION, Question(id=qid, text=body.question), namespace=rec.namespace)
    return {"id": qid, "status": "pending"}


@app.get("/v1/ask/{qid}")
async def ask_status(qid: str, request: Request, rec: TaskRec = Depends(task_of)):
    ans = await request.app.state.orch.query(rec.workflow_id, Q_ANSWER, qid, namespace=rec.namespace)
    return {"id": qid, **(ans if isinstance(ans, dict) else asdict(ans))}


MAX_DOCUMENT_BYTES = 100_000


class ResultIn(BaseModel):
    patch: str = Field("", max_length=MAX_RESULT_BYTES)  # empty for the roles that only write a document
    summary: str = Field("", max_length=5000)
    title: str = Field("", max_length=200)
    body: str = Field("", max_length=20000)
    documents: dict[str, str] = Field(default_factory=dict, max_length=4)


@app.post("/v1/result", status_code=202)
async def result(body: ResultIn, request: Request, rec: TaskRec = Depends(task_of)):
    for name, text in body.documents.items():
        if name not in H.OUTPUT_FILE.values():
            raise HTTPException(422, f"unexpected document '{name}'")
        if len(text.encode()) > MAX_DOCUMENT_BYTES:
            raise HTTPException(422, f"{name} is larger than {MAX_DOCUMENT_BYTES // 1000} KB")
    screen_outbound(request.app.state.cfg, rec, body.title, body.body, body.summary, body.patch, *body.documents.values())
    await request.app.state.orch.signal(rec.workflow_id, SIG_DONE, AgentResult(**body.model_dump()), namespace=rec.namespace)
    return {"accepted": True}


class FailIn(BaseModel):
    message: str = Field(max_length=2000)


@app.post("/v1/fail", status_code=202)
async def fail(body: FailIn, request: Request, rec: TaskRec = Depends(task_of)):
    await request.app.state.orch.signal(rec.workflow_id, SIG_FAILED, body.message, namespace=rec.namespace)
    return {"accepted": True}


class LogIn(BaseModel):
    lines: list[str] = Field(max_length=200)


@app.post("/v1/log", status_code=202)
async def log_lines(body: LogIn, request: Request, rec: TaskRec = Depends(task_of)):
    """Agent output for the dashboard. Secrets are masked; the text is never trusted or acted on."""
    clean = [redact(l)[:600] for l in body.lines if l.strip()][:200]
    if clean:
        await request.app.state.orch.signal(rec.workflow_id, SIG_LOG, clean, namespace=rec.namespace)
    return {"accepted": len(clean)}


@app.get("/v1/budget")
async def budget(rec: TaskRec = Depends(task_of)):
    """Lets the runner see that a pause is a budget question and wait for its answer."""
    exceeded = rec.spent_usd >= rec.budget_usd
    return {"exceeded": exceeded, "ask_id": rec.budget_ask_id if exceeded else None, "budget_usd": rec.budget_usd, "spent_usd": round(rec.spent_usd, 4)}


class LessonIn(BaseModel):
    text: str = Field(min_length=10, max_length=500)


@app.post("/v1/lessons", status_code=202)
async def suggest_lesson(body: LessonIn, request: Request, rec: TaskRec = Depends(task_of)):
    """The agent may suggest a rule. It is always queued for human review: an agent cannot approve its own instructions."""
    cfg = request.app.state.cfg
    screen_outbound(cfg, rec, body.text)
    r = await request.app.state.http.post(f"{cfg.portal_api}/api/lessons", headers={"Authorization": f"Bearer {cfg.ingest_token}"}, timeout=10, json={
        "text": body.text, "scope": "repo", "repo": rec.repo, "source": "agent_suggestion", "author": f"agent:{rec.task_id}", "author_role": "member", "ticket_key": rec.task_id})
    return {"queued": r.status_code == 201}


# ------------------------------------------------------------------ LLM proxy with a hard budget
def usage_of(data: dict) -> tuple[int, int]:
    u = data.get("usage") or {}
    return int(u.get("input_tokens", u.get("prompt_tokens", 0)) or 0), int(u.get("output_tokens", u.get("completion_tokens", 0)) or 0)


async def charge(request: Request, rec: TaskRec, model: str, tin: int, tout: int, reported_cost: float | None) -> None:
    cfg, st = request.app.state.cfg, request.app.state
    if reported_cost is not None:
        cost = reported_cost
    else:
        cost, est = cost_of(model, tin, tout, cfg.prices)
        rec.estimated = rec.estimated or est
    rec.spent_usd += cost
    try:
        await st.orch.signal(rec.workflow_id, SIG_COST, round(rec.spent_usd, 6), namespace=rec.namespace)
        if rec.spent_usd >= rec.budget_usd and rec.budget_ask_id is None:
            rec.budget_ask_id = uuid.uuid4().hex[:12]
            text = f"Budget of ${rec.budget_usd:.2f} reached (spent ${rec.spent_usd:.2f}). Reply with 'approve' to add more budget, or 'stop' to end the run."
            await st.orch.signal(rec.workflow_id, SIG_QUESTION, Question(id=rec.budget_ask_id, text=text, reason="budget"), namespace=rec.namespace)
    except Exception:
        log.exception("could not report spend for %s", rec.task_id)


async def proxy(request: Request, rec: TaskRec, path: str) -> Response:
    cfg, st = request.app.state.cfg, request.app.state
    if not cfg.llm_url:
        raise HTTPException(503, "no LLM is configured (LLM_UPSTREAM_URL)")
    if rec.spent_usd >= rec.budget_usd:
        return Response(json.dumps({"error": "budget_exceeded", "ask_id": rec.budget_ask_id, "message": "Budget reached. Wait for a human decision: GET /v1/ask/{ask_id}."}),
                        status_code=402, media_type="application/json")
    payload = await request.json()
    model = str(payload.get("model", ""))
    if cfg.models and model not in cfg.models:
        raise HTTPException(403, f"model '{model}' is not allowed")
    headers = {"content-type": "application/json", **({"anthropic-version": request.headers["anthropic-version"]} if "anthropic-version" in request.headers else {})}
    headers |= {"x-api-key": cfg.llm_key} if cfg.llm_style == "x-api-key" else {"authorization": f"Bearer {cfg.llm_key}"}  # the sandbox's token never goes upstream
    url = cfg.llm_url + path

    if payload.get("stream"):
        upstream = await st.http.send(st.http.build_request("POST", url, json=payload, headers=headers), stream=True)

        async def relay():
            tin = tout = 0
            try:
                async for line in upstream.aiter_lines():
                    if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                        try:
                            ev = json.loads(line[5:])
                            a, b = usage_of(ev.get("message", ev))  # Anthropic message_start carries input tokens; message_delta output tokens
                            tin, tout = max(tin, a), max(tout, b)
                            if "usage" in ev:
                                a, b = usage_of(ev)
                                tin, tout = max(tin, a), max(tout, b)
                        except ValueError:
                            pass
                    yield (line + "\n").encode()
            finally:
                await upstream.aclose()
                await charge(request, rec, model, tin, tout, None)

        return StreamingResponse(relay(), status_code=upstream.status_code, media_type=upstream.headers.get("content-type", "text/event-stream"))

    r = await st.http.post(url, json=payload, headers=headers)
    if r.status_code < 300:
        try:
            tin, tout = usage_of(r.json())
        except ValueError:
            tin = tout = 0
        reported = r.headers.get("x-litellm-response-cost")
        await charge(request, rec, model, tin, tout, float(reported) if reported else None)
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


@app.post("/v1/messages")
async def anthropic_messages(request: Request, rec: TaskRec = Depends(task_of)):
    return await proxy(request, rec, "/v1/messages")


@app.post("/v1/chat/completions")
async def openai_chat(request: Request, rec: TaskRec = Depends(task_of)):
    return await proxy(request, rec, "/v1/chat/completions")
