# Build context is the repo root: docker build -f components/git-broker/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml pyproject.toml
COPY libs/contracts libs/contracts
COPY components/injection-guard components/injection-guard
COPY components/git-broker components/git-broker
RUN uv pip install --system ./libs/contracts ./components/injection-guard ./components/git-broker \
 && mkdir /data && chown nobody /data
ENV GIT_BROKER_DATA=/data
USER nobody
EXPOSE 8080
CMD ["uvicorn", "git_broker.main:app", "--host", "0.0.0.0", "--port", "8080"]
