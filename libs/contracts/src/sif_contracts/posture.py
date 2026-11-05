"""What must be true before the factory runs in production (SIF_ENV=production). Each service checks its part when it starts and refuses to
start otherwise: a setting that is merely documented gets missed, a service that will not start does not."""

import os

MIN_TOKEN = 24


def production(env: dict[str, str] | None = None) -> bool:
    return (os.environ if env is None else env).get("SIF_ENV", "").lower() == "production"


def problems(service: str, env: dict[str, str] | None = None) -> list[str]:
    """Why this service must not start in production. Empty outside production, or when everything is in place."""
    env = dict(os.environ if env is None else env)
    if not production(env):
        return []
    out: list[str] = []

    def token(name: str) -> None:
        if len(env.get(name, "")) < MIN_TOKEN:
            out.append(f"{name} must be set to a random value of at least {MIN_TOKEN} characters")

    if service in ("portal-api", "jira-connector", "orchestrator-worker", "sandbox-gateway", "context-ingest"):
        token("SIF_INGEST_TOKEN")
    if service in ("portal-api", "git-broker", "jira-connector", "context-ingest", "sandbox-provisioner", "orchestrator-worker"):
        token("SIF_INTERNAL_TOKEN")
    if service == "portal-api":
        token("SIF_DASHBOARD_TOKEN")
        if env.get("SIF_DASHBOARD_TOKEN") and env.get("SIF_DASHBOARD_TOKEN") in (env.get("SIF_INGEST_TOKEN"), env.get("SIF_INTERNAL_TOKEN")):
            out.append("SIF_DASHBOARD_TOKEN must differ from the service tokens, or a service could make people's decisions")
        if not env.get("SIF_SECRET_KEY"):
            out.append("SIF_SECRET_KEY must be set (connection secrets are encrypted with it)")
        if env.get("SIF_LESSON_AUTO_APPROVE", "true").lower() not in ("0", "false", "no"):
            out.append("SIF_LESSON_AUTO_APPROVE must be false: lessons become agent instructions, so a person approves each one "
                       "(a lead's role comes from a service, and a leaked service token could otherwise add one)")
        for name in ("SIF_RETENTION_DAYS", "SIF_AUDIT_RETENTION_DAYS"):
            if not env.get(name, "").strip().isdigit():
                out.append(f"{name} must be set to a number of days (0 keeps everything): decide how long ticket text, code and audit rows are kept")
    if "sif-local-dev" in env.get("DATABASE_URL", ""):
        out.append("the database still uses the development password (POSTGRES_PASSWORD)")
    if service == "git-broker":
        if not env.get("GITHUB_APP_ID") and env.get("GITHUB_TOKEN"):
            out.append("use a GitHub App (GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY) instead of a personal GITHUB_TOKEN")
        if env.get("SIF_REQUIRE_PROTECTED_BRANCH", "true").lower() not in ("1", "true", "yes"):
            out.append("SIF_REQUIRE_PROTECTED_BRANCH cannot be turned off in production")
        if env.get("SIF_ALLOW_DEPENDENCY_CHANGES", "").lower() in ("1", "true", "yes"):
            out.append("SIF_ALLOW_DEPENDENCY_CHANGES cannot be on in production; a person changes dependencies")
    if service == "sandbox-provisioner" and env.get("SIF_SANDBOX_BACKEND", "podman") == "podman" and not env.get("SIF_PODMAN_RUNTIME"):
        out.append("sandboxes on Podman need a gVisor runtime in production (SIF_PODMAN_RUNTIME=runsc), or use SIF_SANDBOX_BACKEND=kubernetes")
    if service in ("orchestrator-worker", "sandbox-gateway") and env.get("SIF_AGENT", "") == "scripted":
        out.append("SIF_AGENT=scripted is the demo agent")
    return out


def enforce(service: str) -> None:
    """Exits with the reasons if this service may not start."""
    if found := problems(service):
        raise SystemExit(f"{service} will not start with SIF_ENV=production:\n- " + "\n- ".join(found))
