# Build context is the repo root: docker build -f components/portal-api/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml pyproject.toml
COPY libs/contracts libs/contracts
COPY components/injection-guard components/injection-guard
COPY libs/context libs/context
COPY components/portal-api components/portal-api
RUN uv pip install --system ./libs/contracts ./components/injection-guard ./libs/context ./components/portal-api
USER nobody
EXPOSE 8000
CMD ["uvicorn", "portal_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
