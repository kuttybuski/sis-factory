# Build context is the repo root: docker build -f components/jira-connector/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml pyproject.toml
COPY libs/contracts libs/contracts
COPY components/jira-connector components/jira-connector
RUN uv pip install --system ./libs/contracts ./components/jira-connector
USER nobody
EXPOSE 8080
CMD ["uvicorn", "jira_connector.main:app", "--host", "0.0.0.0", "--port", "8080"]
