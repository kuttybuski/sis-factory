# Build context is the repo root: docker build -f components/context-api/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml pyproject.toml
COPY libs/contracts libs/contracts
COPY components/injection-guard components/injection-guard
COPY libs/context libs/context
COPY components/context-api components/context-api
RUN uv pip install --system ./libs/contracts ./components/injection-guard ./libs/context ./components/context-api
USER nobody
EXPOSE 8080
CMD ["uvicorn", "context_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
