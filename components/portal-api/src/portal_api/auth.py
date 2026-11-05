"""Who may call portal-api. Reads are open to the dashboard; every write needs a token, so no browser page (and no other website,
through the user's browser) can change anything.

Two write tokens, so a compromised service cannot make a person's decisions:
- SIS_DASHBOARD_TOKEN is held by the dashboard server only. Decisions people make (approving lessons, editing teams, team knowledge,
  handling, connections, killing tasks, pausing intake) need it.
- SIS_INGEST_TOKEN is held by the services. It reports events, proposes lessons, claims replay runs and knowledge PRs. It also works
  on the dashboard routes until SIS_DASHBOARD_TOKEN is set (local development); in production it never does (sis_contracts.posture).
Only services that need a team's decrypted credentials hold SIS_INTERNAL_TOKEN."""

import os
import secrets

from fastapi import Header, HTTPException, Request


def pool(request: Request):
    return request.app.state.pool


def _matches(authorization: str | None, *env_names: str) -> bool:
    given = (authorization or "").removeprefix("Bearer ")
    return any((t := os.environ.get(n, "")) and secrets.compare_digest(given, t) for n in env_names)


def _token_guard(*env_names: str):
    def guard(authorization: str | None = Header(default=None)) -> None:
        if not any(os.environ.get(n, "") for n in env_names):
            raise HTTPException(503, f"{env_names[0]} is not configured")  # secure default: no token, no access
        if not _matches(authorization, *env_names):
            raise HTTPException(401, "invalid token")
    return guard


def dashboard_guard(authorization: str | None = Header(default=None)) -> None:
    """A decision by a person, through the dashboard."""
    if not os.environ.get("SIS_DASHBOARD_TOKEN", ""):
        return _token_guard("SIS_INGEST_TOKEN")(authorization)
    if _matches(authorization, "SIS_DASHBOARD_TOKEN"):
        return None
    if _matches(authorization, "SIS_INGEST_TOKEN"):
        raise HTTPException(403, "only the dashboard may do this")
    raise HTTPException(401, "invalid token")


write_guard = _token_guard("SIS_INGEST_TOKEN", "SIS_DASHBOARD_TOKEN")  # services and the dashboard
internal_guard = _token_guard("SIS_INTERNAL_TOKEN")
