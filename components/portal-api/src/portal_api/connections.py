"""Per-team connections to Jira and GitHub: what the dashboard saves, and what the services fetch.

Secrets are encrypted at rest (vault.py) and never returned by the public endpoints. The internal endpoint hands a decrypted secret only to a caller
holding SIS_INTERNAL_TOKEN, which is how jira-connector, git-broker and context-ingest get a team's credentials."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sis_contracts import Settings
from sis_contracts import connections as C

from portal_api import connection_check, db, vault
from portal_api.auth import dashboard_guard, internal_guard, pool

router = APIRouter()

CONFIG_KEYS = {"jira": {"base_url", "email"}, "github": {"api_url", "git_url"}}
URL_KEYS = {"base_url", "api_url", "git_url"}
# Changing any of these would send the stored secret somewhere new, so the secret must be entered again with them.
SECRET_BOUND = {"jira": {"base_url", "email"}, "github": {"api_url", "git_url"}}


class ConnectionIn(BaseModel):
    config: dict[str, str] = {}
    secret: str | None = None  # blank or missing keeps the stored secret
    by: str = "dashboard"


def _provider(provider: str) -> str:
    if provider not in C.PROVIDERS:
        raise HTTPException(404, f"unknown provider '{provider}'")
    return provider


async def _team(p, name: str) -> dict[str, Any]:
    team = next((t for t in await db.list_teams(p) if t["name"] == name), None)
    if team is None:
        raise HTTPException(404, "team not found")
    return team


@router.get("/api/connections")
async def list_connections(p=Depends(pool)):
    return {"vault": vault.available(), "allow_private_urls": Settings().allow_private_urls, "connections": await db.list_connections(p)}


async def _check(p, team: dict[str, Any], provider: str, config: dict[str, Any], secret: str) -> dict[str, Any] | None:
    if provider == "jira":
        status, detail = await connection_check.check_jira(C.JiraCreds(config["base_url"], config["email"], secret), team.get("jira_projects") or [])
    else:
        status, detail = await connection_check.check_github(
            C.GithubCreds(secret, config.get("api_url") or C.DEFAULT_GITHUB_API, config.get("git_url") or C.DEFAULT_GITHUB_GIT), team.get("repos") or [])
    return await db.set_connection_status(p, team["name"], provider, status, detail)


@router.put("/api/connections/{team}/{provider}", dependencies=[Depends(dashboard_guard)])
async def save_connection(team: str, provider: str, body: ConnectionIn, p=Depends(pool)):
    _provider(provider)
    t = await _team(p, team)
    config = {k: v.strip() for k, v in body.config.items() if v is not None and v.strip()}
    if extra := sorted(set(config) - CONFIG_KEYS[provider]):
        raise HTTPException(422, f"unknown settings for {provider}: {', '.join(extra)}")
    for k in URL_KEYS & set(config):
        config[k] = C.normalise_url(config[k])
        if err := await asyncio.to_thread(C.check_url, config[k], allow_private=Settings().allow_private_urls):
            raise HTTPException(422, f"{k}: {err}")
    if provider == "jira":
        for k in ("base_url", "email"):
            if not config.get(k):
                raise HTTPException(422, f"{k} is required")
    new_secret = (body.secret or "").strip() or None
    if new_secret and (len(new_secret) > 500 or any(c.isspace() for c in new_secret)):
        raise HTTPException(422, "the token looks wrong: it should be one line with no spaces")
    existing = await db.get_connection(p, team, provider)
    if new_secret is None:
        if not existing or not existing["has_secret"]:
            raise HTTPException(422, "a token is required")
        changed = sorted(k for k in SECRET_BOUND[provider] if config.get(k) != (existing["config"] or {}).get(k))
        if changed:
            raise HTTPException(422, f"you changed {', '.join(changed)}, so enter the token again: a stored token is never sent to a new address")
    try:
        blob, hint = (vault.encrypt(new_secret), vault.hint(new_secret)) if new_secret else (None, None)
    except vault.VaultError as e:
        raise HTTPException(503, str(e)) from None
    await db.put_connection(p, team=team, provider=provider, config=config, secret=blob, hint=hint, by=body.by)
    return await _test(p, t, provider)


async def _test(p, team: dict[str, Any], provider: str) -> dict[str, Any]:
    stored = await db.get_connection_secret(p, team["name"], provider)
    if stored is None:
        raise HTTPException(404, "no connection saved for this team")
    try:
        row = await _check(p, team, provider, stored["config"], vault.decrypt(stored["secret"]))
    except vault.VaultError as e:
        row = await db.set_connection_status(p, team["name"], provider, "failed", str(e))
    return row


@router.post("/api/connections/{team}/{provider}/test", dependencies=[Depends(dashboard_guard)])
async def test_connection(team: str, provider: str, p=Depends(pool)):
    _provider(provider)
    return await _test(p, await _team(p, team), provider)


@router.delete("/api/connections/{team}/{provider}", dependencies=[Depends(dashboard_guard)])
async def remove_connection(team: str, provider: str, p=Depends(pool)):
    _provider(provider)
    await _team(p, team)
    return {"removed": await db.delete_connection(p, team, provider)}


@router.get("/api/internal/connections/{team}/{provider}", dependencies=[Depends(internal_guard)])
async def internal_connection(team: str, provider: str, p=Depends(pool)):
    """The one place a decrypted secret leaves portal-api. 404 means the team has no connection and the caller uses its shared credentials."""
    _provider(provider)
    stored = await db.get_connection_secret(p, team, provider)
    if stored is None:
        raise HTTPException(404, "no connection for this team")
    try:
        return {"config": stored["config"], "secret": vault.decrypt(stored["secret"])}
    except vault.VaultError as e:
        raise HTTPException(500, str(e)) from None
