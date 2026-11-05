# Build context is the repo root: docker build -f components/agent-runner/Dockerfile -t localhost/sif-agent-runner:latest .
# Add the proprietary Claude Code CLI with --build-arg INSTALL_CLAUDE=true (subject to Anthropic's terms).
FROM docker.io/library/python:3.12-slim
ARG INSTALL_CLAUDE=false
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN if [ "$INSTALL_CLAUDE" = "true" ]; then apt-get update && apt-get install -y --no-install-recommends nodejs npm && npm install -g @anthropic-ai/claude-code && rm -rf /var/lib/apt/lists/*; fi
COPY components/agent-runner /src
RUN pip install --no-cache-dir /src
# Runs as a non-root user on a read-only root filesystem; /workspace and /tmp are tmpfs mounts set by the provisioner.
RUN useradd -u 10001 -m -d /workspace agent || true
USER 10001
ENV SIF_WORKSPACE=/workspace HOME=/workspace
WORKDIR /workspace
CMD ["sif-agent"]
