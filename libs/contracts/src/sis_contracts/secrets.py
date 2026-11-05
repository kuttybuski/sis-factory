"""Secrets from files. Every secret setting can also be given as NAME_FILE, the path of a file holding it: what Kubernetes Secrets,
External Secrets, Vault Agent and Docker/Podman secrets mount. The file wins over nothing; a value set directly wins over the file.

load_secret_files() runs when sis_contracts is imported, so every Python service reads NAME as before."""

import os
from pathlib import Path

SECRETS = ("SIS_INGEST_TOKEN", "SIS_INTERNAL_TOKEN", "SIS_DASHBOARD_TOKEN", "SIS_SECRET_KEY", "SIS_SECRET_KEYS_OLD", "GITHUB_TOKEN",
           "GITHUB_APP_PRIVATE_KEY", "JIRA_API_TOKEN", "CONFLUENCE_TOKEN", "LLM_API_KEY", "ANTHROPIC_API_KEY", "EMBEDDINGS_API_KEY",
           "POSTGRES_PASSWORD", "DATABASE_URL")


def env_secret(name: str, env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    if v := env.get(name, ""):
        return v
    if path := env.get(f"{name}_FILE", ""):
        return Path(path).read_text().strip()
    return ""


def load_secret_files(env: dict[str, str] | None = None) -> list[str]:
    """Copies NAME_FILE contents into NAME for the known secrets that are not set directly. Returns the names it loaded."""
    env = os.environ if env is None else env
    loaded = []
    for name in SECRETS:
        if not env.get(name) and env.get(f"{name}_FILE"):
            env[name] = env_secret(name, env)
            loaded.append(name)
    return loaded
