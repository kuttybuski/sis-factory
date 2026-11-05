"""Prompt-injection incidents: orchestrator-worker reports each attempt it catches, opens a security PR on the team's repository for the
new ones (orchestrator_worker.security), and reads the decision back. Merged fingerprints are blocked for the team from then on."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from portal_api import db
from portal_api.auth import pool, write_guard

router = APIRouter()


class IncidentIn(BaseModel):
    team: str = ""
    repo: str = ""
    jira_key: str = Field(max_length=64)
    source: str = Field(max_length=40)
    verdict: Literal["flag", "block"]
    score: int
    rules: list[str] = Field(default=[], max_length=50)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor: str = Field("", max_length=200)


@router.post("/api/security/incidents", dependencies=[Depends(write_guard)])
async def record_incident(body: IncidentIn, p=Depends(pool)):
    return await db.record_incident(p, **body.model_dump())


@router.get("/api/security/incidents")
async def incidents(team: str | None = None, status: str | None = None, p=Depends(pool)):
    return await db.list_incidents(p, team=team, status=status)


@router.get("/api/security/blocklist")
async def blocklist(team: str, p=Depends(pool)):
    return {"fingerprints": await db.team_blocklist(p, team)}


@router.post("/api/security/claim", dependencies=[Depends(write_guard)])
async def claim(p=Depends(pool)):
    """Repositories due a security PR, each with the attempts it should record."""
    return await db.claim_incidents(p)


class OpenedIn(BaseModel):
    ids: list[int]
    pr_number: int
    pr_url: str
    branch: str


@router.post("/api/security/opened", dependencies=[Depends(write_guard)])
async def opened(body: OpenedIn, p=Depends(pool)):
    return {"updated": await db.incidents_opened(p, body.ids, pr_number=body.pr_number, pr_url=body.pr_url, branch=body.branch)}


class DecidedIn(BaseModel):
    ids: list[int]
    state: Literal["merged", "closed", "failed"]
    by: str | None = None
    error: str | None = None


@router.post("/api/security/decided", dependencies=[Depends(write_guard)])
async def decided(body: DecidedIn, p=Depends(pool)):
    return {"updated": await db.decide_incidents(p, body.ids, state=body.state, by=body.by, error=body.error)}
