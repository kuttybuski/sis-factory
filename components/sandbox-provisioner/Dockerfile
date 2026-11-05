# Build context is the repo root: docker build -f components/sandbox-provisioner/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml pyproject.toml
COPY components/sandbox-provisioner components/sandbox-provisioner
RUN uv pip install --system ./components/sandbox-provisioner
# Locally this must run as root INSIDE its container (a normal user under rootless Podman) to reach the Podman socket.
# In a cluster it runs non-root with a service account limited to pods in the sandbox namespace.
EXPOSE 8080
CMD ["uvicorn", "sandbox_provisioner.main:app", "--host", "0.0.0.0", "--port", "8080"]
