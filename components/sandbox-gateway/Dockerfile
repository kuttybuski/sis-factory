# Build context is the repo root: docker build -f components/sandbox-gateway/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml pyproject.toml
COPY libs/contracts libs/contracts
COPY components/injection-guard components/injection-guard
COPY components/sandbox-gateway components/sandbox-gateway
RUN uv pip install --system ./libs/contracts ./components/injection-guard ./components/sandbox-gateway
USER nobody
EXPOSE 8080
CMD ["uvicorn", "sandbox_gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
