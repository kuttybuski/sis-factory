"""Local embeddings. Same request/response shape as OpenAI's /v1/embeddings, so a hosted provider is a drop-in replacement.

EMBEDDINGS_BACKEND=fastembed (default) runs an open-source ONNX model on CPU; the weights are downloaded once into EMBEDDINGS_CACHE.
EMBEDDINGS_BACKEND=hash is an offline fallback with no model (matches shared words, not meaning)."""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from sif_context.embed import HashEmbedder

BACKEND = os.environ.get("EMBEDDINGS_BACKEND", "fastembed")
MODEL = os.environ.get("EMBEDDINGS_MODEL", "BAAI/bge-small-en-v1.5")
CACHE = os.environ.get("EMBEDDINGS_CACHE", "/models")
MODEL_PATH = os.environ.get("EMBEDDINGS_MODEL_PATH", "")  # a pre-downloaded model directory: runs with no internet at all
FALLBACK = os.environ.get("EMBEDDINGS_FALLBACK", "")  # "hash": if the model cannot be loaded, serve the offline fallback and say so
MAX_INPUTS, MAX_CHARS = 256, 20_000

state: dict = {"ready": False, "error": None, "dim": None, "degraded": False, "backend": BACKEND,
               "model": MODEL if BACKEND == "fastembed" else HashEmbedder.model}
_model = None


def _load() -> None:
    global _model
    try:
        if BACKEND == "hash":
            state.update(ready=True, dim=HashEmbedder().dim)
            return
        from fastembed import TextEmbedding

        kwargs = {"specific_model_path": MODEL_PATH} if MODEL_PATH else {}
        _model = TextEmbedding(model_name=MODEL, cache_dir=CACHE, **kwargs)
        state.update(ready=True, dim=len(next(iter(_model.embed(["warm up"])))))
    except Exception as e:  # reported by /healthz so a failed download is visible, not silent
        state["error"] = f"{type(e).__name__}: {e}"
        if FALLBACK == "hash":
            state.update(ready=True, error=None, degraded=True, backend="hash", model=HashEmbedder.model, dim=HashEmbedder().dim,
                         reason=f"model could not be loaded, serving the offline hash fallback: {e}"[:400])


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.get_running_loop().run_in_executor(None, _load)  # the first start downloads the model: do not block the server
    yield
    task.cancel()


app = FastAPI(title="sif embeddings", lifespan=lifespan)


class EmbeddingsRequest(BaseModel):
    model: str | None = None
    input: str | list[str] = Field(...)


@app.get("/healthz")
def healthz():
    if state["error"]:
        raise HTTPException(500, state["error"])
    if not state["ready"]:
        raise HTTPException(503, "model is loading")
    return {"ok": True, "backend": state["backend"], "model": state["model"], "dim": state["dim"], "degraded": state["degraded"], "reason": state.get("reason")}


@app.post("/v1/embeddings")
async def embeddings(req: EmbeddingsRequest):
    if not state["ready"]:
        raise HTTPException(503, state["error"] or "model is loading")
    texts = [req.input] if isinstance(req.input, str) else req.input
    if not texts or len(texts) > MAX_INPUTS or any(len(t) > MAX_CHARS for t in texts):
        raise HTTPException(422, f"send 1-{MAX_INPUTS} inputs of at most {MAX_CHARS} characters")
    if state["backend"] == "hash":
        vectors = await HashEmbedder().embed(texts)
    else:
        vectors = await asyncio.get_running_loop().run_in_executor(None, lambda: [v.tolist() for v in _model.embed(texts)])
    tokens = sum(len(t) // 4 for t in texts)
    return {"object": "list", "model": state["model"], "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
            "usage": {"prompt_tokens": tokens, "total_tokens": tokens}}
