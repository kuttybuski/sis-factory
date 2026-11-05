# Build context is the repo root: docker build -f components/context-ingest/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml pyproject.toml
COPY libs/contracts libs/contracts
COPY components/injection-guard components/injection-guard
COPY libs/context libs/context
COPY components/context-ingest components/context-ingest
RUN uv pip install --system ./libs/contracts ./components/injection-guard ./libs/context ./components/context-ingest
USER nobody
EXPOSE 8080
CMD ["uvicorn", "context_ingest.main:app", "--host", "0.0.0.0", "--port", "8080"]
