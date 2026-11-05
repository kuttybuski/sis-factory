"""Teams. A team is a Temporal namespace, named after it ("Mobile Apps" -> mobile-apps): its tickets run as workflows there, apart from
other teams' in the Temporal UI. Saving a team registers the namespace; the workers start serving it on their next sync.

Writes need the ingest token (the dashboard server holds it). Deleting a team removes its handling overrides and connections; its
Temporal namespace and task history stay (Temporal keeps finished workflows for the retention period)."""

import os
import re
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from sis_contracts.namespaces import Namespaces, check_namespace, namespace_for

from portal_api import db
from portal_api.auth import dashboard_guard, pool

router = APIRouter()

PROJECT = re.compile(r"^[A-Z][A-Z0-9_]{1,9}$")
SPACE = re.compile(r"^[A-Za-z0-9~_-]{1,64}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LEAD = re.compile(r"^[A-Za-z0-9_.+@:/ -]{2,200}$")  # an email or an IdP group name


def _clean(values: list[str], pattern: re.Pattern, what: str) -> list[str]:
    out = []
    for v in (v.strip() for v in values):
        if v and not pattern.match(v):
            raise ValueError(f"{v!r} is not a valid {what}")
        if v and v not in out:
            out.append(v)
    return out


class TeamIn(BaseModel):
    name: str
    jira_projects: list[str] = []
    confluence_spaces: list[str] = []
    repos: list[str] = []  # owner/name of GitHub repositories whose PRs and docs feed the context index
    leads: list[str] = []  # emails or IdP groups who act for the team on the dashboard (knowledge, lessons, tasks); empty = the global operators

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not namespace_for(v) or len(v) > 60:
            raise ValueError("name must be 1-60 characters and contain a letter or digit")
        if problem := check_namespace(namespace_for(v)):
            raise ValueError(problem)
        return v

    @field_validator("jira_projects")
    @classmethod
    def _projects(cls, v: list[str]) -> list[str]:
        return _clean([p.upper() for p in v], PROJECT, "Jira project key (e.g. PAY)")

    @field_validator("confluence_spaces")
    @classmethod
    def _spaces(cls, v: list[str]) -> list[str]:
        return _clean(v, SPACE, "Confluence space key")

    @field_validator("repos")
    @classmethod
    def _repos(cls, v: list[str]) -> list[str]:
        return _clean(v, REPO, "repository (owner/name)")

    @field_validator("leads")
    @classmethod
    def _leads(cls, v: list[str]) -> list[str]:
        return _clean([x.lower() for x in v], LEAD, "email or group name")


def namespaces(request: Request) -> Namespaces | None:
    """Temporal, for registering namespaces. None when TEMPORAL_ADDRESS is not set (a portal run on its own)."""
    if not hasattr(request.app.state, "namespaces"):
        addr = os.environ.get("TEMPORAL_ADDRESS", "")
        request.app.state.namespaces = Namespaces(addr) if addr else None
    return request.app.state.namespaces


@router.get("/api/teams")
async def list_teams(p=Depends(pool)):
    return await db.list_teams(p)


@router.put("/api/teams", dependencies=[Depends(dashboard_guard)])
async def put_team(team: TeamIn, request: Request, p=Depends(pool)) -> dict[str, Any]:
    ns, others = namespace_for(team.name), [t for t in await db.list_teams(p) if t["name"] != team.name]
    if clash := next((t["name"] for t in others if t["temporal_namespace"] == ns), None):
        raise HTTPException(409, f"{clash} already uses namespace {ns}; choose a name that differs by more than case or punctuation")
    if clashes := [f"{k} is {t['name']}'s" for t in others for k in t["jira_projects"] if k in team.jira_projects]:
        raise HTTPException(409, "a Jira project can belong to one team only: " + ", ".join(clashes))
    created = False
    if (temporal := namespaces(request)) is not None:
        try:
            created = await temporal.ensure(ns)
        except Exception as e:
            raise HTTPException(503, f"Temporal could not create namespace {ns} ({type(e).__name__}); the team was not saved") from e
    return {**await db.upsert_team(p, **team.model_dump()), "namespace_created": created}


@router.delete("/api/teams/{name}", dependencies=[Depends(dashboard_guard)])
async def delete_team(name: str, p=Depends(pool)) -> dict[str, Any]:
    if n := await db.active_tasks(p, name):
        raise HTTPException(409, f"{name} has {n} running task(s); stop them or wait for them to finish")
    if not await db.delete_team(p, name):
        raise HTTPException(404, "no such team")
    return {"deleted": name}


_POSTURE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


@router.get("/api/teams/{name}/posture")
async def team_posture(name: str, p=Depends(pool)) -> list[dict[str, Any]]:
    """For each of the team's repositories: which GitHub identity the factory uses there and whether the default branch requires an
    approving review (git-broker /v1/posture). Kept for five minutes; empty when git-broker is not configured."""
    team = next((t for t in await db.list_teams(p) if t["name"] == name), None)
    if team is None:
        raise HTTPException(404, "no such team")
    broker, token = os.environ.get("GIT_BROKER_URL", ""), os.environ.get("SIS_INTERNAL_TOKEN", "")
    if not broker or not token or not team["repos"]:
        return []
    if (hit := _POSTURE.get(name)) and hit[0] > time.time():
        return hit[1]
    out = []
    async with httpx.AsyncClient(base_url=broker, timeout=20, headers={"Authorization": f"Bearer {token}"}) as c:
        for repo in team["repos"]:
            try:
                r = await c.get(f"/v1/posture/{repo}", params={"team": name})
                out.append(r.json() if r.status_code == 200 else {"repo": repo, "ok": False, "problems": [f"git-broker answered {r.status_code}: {r.text[:200]}"]})
            except httpx.HTTPError as e:
                out.append({"repo": repo, "ok": False, "problems": [f"git-broker is not reachable ({type(e).__name__})"]})
    _POSTURE[name] = (time.time() + 300, out)
    return out
