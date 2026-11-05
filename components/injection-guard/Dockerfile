# Build context is the repo root: docker build -f components/injection-guard/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml pyproject.toml
COPY components/injection-guard components/injection-guard
RUN uv pip install --system ./components/injection-guard
USER nobody
EXPOSE 8080
CMD ["uvicorn", "injection_guard.service:app", "--host", "0.0.0.0", "--port", "8080"]
