"""Stopping and limiting intake: the switch for "the factory takes no new tickets", for every team or one, and the counts jira-connector
uses to enforce its per-team limits. A pause does not stop running tasks (Kill does); tickets wait with their label until it is lifted."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import dataclasses
import os

from sif_contracts.intake import ALL, Limits, held_back

from portal_api import db
from portal_api.auth import dashboard_guard, pool

router = APIRouter()


@router.get("/api/intake")
async def intake(p=Depends(pool)):
    """Pauses, the per-team limits, each team's counts, and why a team's next ticket waits (held; "" = tickets without a team)."""
    pauses, counts = await db.list_pauses(p), await db.intake_counts(p)
    out = {"paused_all": any(x["team"] == ALL for x in pauses), "pauses": pauses, "counts": counts,
           "limits": dataclasses.asdict(Limits.from_env(os.environ))}
    teams = {t["name"] for t in await db.list_teams(p)} | set(counts)
    out["held"] = {t: why for t in sorted(teams) if (why := held_back(t, out))}
    return out


class PauseIn(BaseModel):
    team: str = Field(min_length=1, max_length=60)  # a team's name, or "*" for every team
    reason: str = Field("", max_length=500)
    by: str = "dashboard"


@router.put("/api/intake/pause", dependencies=[Depends(dashboard_guard)])
async def pause(body: PauseIn, p=Depends(pool)):
    if body.team != ALL and not any(t["name"] == body.team for t in await db.list_teams(p)):
        raise HTTPException(404, "no such team")
    return await db.pause_intake(p, team=body.team, reason=body.reason, by=body.by)


@router.delete("/api/intake/pause/{team}", dependencies=[Depends(dashboard_guard)])
async def resume(team: str, p=Depends(pool)):
    if not await db.resume_intake(p, team):
        raise HTTPException(404, "not paused")
    return {"resumed": team}
