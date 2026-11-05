"""Handling policies: how each kind of ticket (epic, feature, story, task) is worked, for the whole factory or one team's override.
jira-connector snapshots the effective policy into each workflow (sif_contracts.handling)."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from injection_guard import Verdict, check_outbound, scan
from sif_contracts import handling as H

from portal_api import db, settings
from portal_api.auth import dashboard_guard, pool

router = APIRouter()
Kind = Literal["epic", "feature", "story", "task"]


class HandlingIn(BaseModel):
    fields: dict[str, Any]
    by: str = "dashboard"


def _handling_maps(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    factory: dict[str, dict[str, Any]] = {}
    teams: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r["scope"] == "factory":
            factory[r["kind"]] = r["fields"]
        elif r["scope"].startswith("team:"):
            teams.setdefault(r["scope"].removeprefix("team:"), {})[r["kind"]] = r["fields"]
    return factory, teams


def _screen_instructions(fields: dict[str, Any]) -> list[str]:
    """The instructions end up in the agent's trusted prompt block, so they get the lesson gates. Returns injection-guard rule ids that only flagged."""
    text = fields.get("instructions")
    if not isinstance(text, str) or not text.strip():
        return []
    verdict = scan(text, "jira_comment")
    flags = [f.rule_id for f in verdict.findings]
    if verdict.verdict is Verdict.BLOCK:
        raise HTTPException(422, "instructions blocked by injection-guard: " + ", ".join(flags))
    if not check_outbound(text).ok:
        raise HTTPException(422, "instructions look like they contain a secret")
    return flags


def _checked(kind: str, factory: dict[str, Any] | None, team: dict[str, Any] | None) -> None:
    unknown = sorted((set(factory or {}) | set(team or {})) - set(H.FIELDS))
    if unknown:
        raise HTTPException(422, f"unknown fields: {', '.join(unknown)}")
    policy, _ = H.resolve(kind, factory, team)
    if errors := H.check(kind, H.as_dict(policy), settings.handling_max_chars):
        raise HTTPException(422, "; ".join(errors))


@router.get("/api/handling")
async def handling(p=Depends(pool)):
    factory, teams = _handling_maps(await db.list_handling(p))
    return {"kinds": list(H.KINDS), "builtin": H.BUILTIN, "defaults": H.as_dict(H.Policy()), "factory": factory, "teams": teams,
            "max_chars": settings.handling_max_chars}


@router.get("/api/handling/effective")
async def handling_effective(kind: Kind, team: str | None = None, p=Depends(pool)):
    """The policy in force for a ticket of this kind in this team, with where each field came from. jira-connector snapshots it into the workflow."""
    factory, teams = _handling_maps(await db.list_handling(p))
    policy, sources = H.resolve(kind, factory.get(kind), teams.get(team or "", {}).get(kind))
    return {"kind": kind, "team": team, "policy": H.as_dict(policy), "sources": sources}


@router.put("/api/handling/factory/{kind}", dependencies=[Depends(dashboard_guard)])
async def put_factory_handling(kind: Kind, body: HandlingIn, p=Depends(pool)):
    flags = _screen_instructions(body.fields)
    _checked(kind, body.fields, None)
    _, teams = _handling_maps(await db.list_handling(p))
    for name, by_kind in teams.items():  # a factory change must not leave a team override that no longer makes sense
        if kind in by_kind:
            try:
                _checked(kind, body.fields, by_kind[kind])
            except HTTPException as e:
                raise HTTPException(409, f"team '{name}' overrides {kind} and would become invalid: {e.detail}") from None
    row = await db.put_handling(p, scope="factory", kind=kind, fields={k: v for k, v in body.fields.items() if k in H.FIELDS}, by=body.by)
    return {**row, "flags": flags}


async def _team_or_404(p, team: str) -> None:
    if team not in {t["name"] for t in await db.list_teams(p)}:
        raise HTTPException(404, "team not found")


@router.put("/api/handling/teams/{team}/{kind}", dependencies=[Depends(dashboard_guard)])
async def put_team_handling(team: str, kind: Kind, body: HandlingIn, p=Depends(pool)):
    await _team_or_404(p, team)
    flags = _screen_instructions(body.fields)
    factory, _ = _handling_maps(await db.list_handling(p))
    _checked(kind, factory.get(kind), body.fields)
    row = await db.put_handling(p, scope=f"team:{team}", kind=kind, fields={k: v for k, v in body.fields.items() if k in H.FIELDS}, by=body.by)
    return {**row, "flags": flags}


@router.delete("/api/handling/teams/{team}/{kind}", dependencies=[Depends(dashboard_guard)])
async def reset_team_handling(team: str, kind: Kind, p=Depends(pool)):
    """Back to inheriting everything from the factory for this kind."""
    await _team_or_404(p, team)
    return {"removed": await db.delete_handling(p, scope=f"team:{team}", kind=kind)}
