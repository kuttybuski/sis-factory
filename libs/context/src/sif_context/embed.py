"""Embedding clients. HttpEmbedder speaks the OpenAI /v1/embeddings shape, so the local embeddings service and a hosted
provider are interchangeable (change EMBEDDINGS_URL / EMBEDDINGS_MODEL / EMBEDDINGS_API_KEY)."""

import hashlib
import math
import re
from typing import Protocol

import httpx

DIM = 384


class Embedder(Protocol):
    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Deterministic bag-of-words feature hashing. Offline, instant, no model: used by tests and as a fallback.
    It matches on shared words, not meaning, so real retrieval quality needs a real model."""

    model = "hash-384"

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        words = re.findall(r"[a-z0-9_]+", text.lower())
        feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
        v = [0.0] * self.dim
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 63) == 0 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


class HttpEmbedder:
    def __init__(self, base_url: str, model: str, api_key: str = "", batch: int = 32, client: httpx.AsyncClient | None = None) -> None:
        self.model = model
        self.batch = batch
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.AsyncClient(base_url=base_url, headers=headers, timeout=120)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            r = await self._client.post("/v1/embeddings", json={"model": self.model, "input": texts[i:i + self.batch]})
            r.raise_for_status()
            body = r.json()
            self.model = body.get("model") or self.model  # tag vectors with the model that really produced them
            data = sorted(body["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
        return out


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def embedder_from_env(env: dict[str, str] | None = None) -> Embedder:
    """EMBEDDINGS_URL set -> HTTP (the local embeddings service or any OpenAI-compatible provider); otherwise the offline hash fallback."""
    import os

    env = os.environ if env is None else env
    if url := env.get("EMBEDDINGS_URL"):
        return HttpEmbedder(url, env.get("EMBEDDINGS_MODEL", "BAAI/bge-small-en-v1.5"), env.get("EMBEDDINGS_API_KEY", ""))
    return HashEmbedder()
