# embeddings

OpenAI-compatible `/v1/embeddings`. Default: `BAAI/bge-small-en-v1.5` on CPU via fastembed (open source). Options for networks that
block the model download: `EMBEDDINGS_MODEL_PATH`, a hosted provider via `EMBEDDINGS_URL`, or `EMBEDDINGS_FALLBACK=hash`.
See `docs/context-and-learning.md`.
