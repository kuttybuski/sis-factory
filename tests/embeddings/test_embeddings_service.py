import importlib
import time

from fastapi.testclient import TestClient


def load(monkeypatch, **env):
    for k in ("EMBEDDINGS_BACKEND", "EMBEDDINGS_FALLBACK", "EMBEDDINGS_MODEL_PATH"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import embeddings_service.main as m

    return importlib.reload(m)


def ready(client, tries=100):
    for _ in range(tries):
        if client.get("/healthz").status_code != 503:
            return client.get("/healthz")
        time.sleep(0.1)


def test_hash_backend_serves_openai_shaped_embeddings(monkeypatch):
    m = load(monkeypatch, EMBEDDINGS_BACKEND="hash")
    with TestClient(m.app) as c:
        h = ready(c).json()
        assert (h["ok"], h["backend"], h["dim"], h["degraded"]) == (True, "hash", 384, False)
        r = c.post("/v1/embeddings", json={"input": ["alpha beta", "gamma"]}).json()
        assert r["object"] == "list" and [d["index"] for d in r["data"]] == [0, 1] and len(r["data"][0]["embedding"]) == 384
        assert r["model"] == "hash-384" and c.post("/v1/embeddings", json={"input": "single"}).json()["data"][0]["index"] == 0


def test_input_limits(monkeypatch):
    m = load(monkeypatch, EMBEDDINGS_BACKEND="hash")
    with TestClient(m.app) as c:
        ready(c)
        assert c.post("/v1/embeddings", json={"input": []}).status_code == 422
        assert c.post("/v1/embeddings", json={"input": ["x"] * 257}).status_code == 422
        assert c.post("/v1/embeddings", json={"input": "x" * 20_001}).status_code == 422


def test_a_model_that_cannot_load_is_reported_not_hidden(monkeypatch):
    m = load(monkeypatch, EMBEDDINGS_MODEL_PATH="/nonexistent")
    with TestClient(m.app) as c:
        for _ in range(100):
            if c.get("/healthz").status_code == 500:
                break
            time.sleep(0.1)
        assert c.get("/healthz").status_code == 500
        assert c.post("/v1/embeddings", json={"input": "x"}).status_code == 503  # never silently serves wrong vectors


def test_explicit_fallback_serves_hash_vectors_and_says_so(monkeypatch):
    m = load(monkeypatch, EMBEDDINGS_MODEL_PATH="/nonexistent", EMBEDDINGS_FALLBACK="hash")
    with TestClient(m.app) as c:
        h = ready(c).json()
        assert h["degraded"] is True and h["backend"] == "hash" and h["model"] == "hash-384" and "could not be loaded" in h["reason"]
        assert c.post("/v1/embeddings", json={"input": "x"}).json()["model"] == "hash-384"
