# Build context is the repo root: docker build -f components/embeddings/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml pyproject.toml
COPY libs/contracts libs/contracts
COPY components/injection-guard components/injection-guard
COPY libs/context libs/context
COPY components/embeddings components/embeddings
RUN uv pip install --system ./libs/contracts ./components/injection-guard ./libs/context ./components/embeddings
ENV EMBEDDINGS_CACHE=/models HF_HOME=/models
RUN mkdir -p /models && chown nobody /models
USER nobody
EXPOSE 8080
CMD ["uvicorn", "embeddings_service.main:app", "--host", "0.0.0.0", "--port", "8080"]
