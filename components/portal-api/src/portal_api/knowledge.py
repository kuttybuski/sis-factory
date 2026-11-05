"""Team knowledge: what a team writes down about how it works, delivered to its agents as trusted instructions (sis_contracts.knowledge).

Writes need the ingest token (the dashboard server holds it, and checks the operator role first). Every save is screened like a lesson,
because the text becomes part of the agent's instructions: an injection attempt or a secret is refused, a suspicious phrase is saved but
flagged. Saves are versioned with optimistic concurrency, so two people editing the same section cannot overwrite each other unseen."""

import hashlib
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from injection_guard import Verdict, check_outbound, scan
from sis_context import improve as I
from sis_contracts import knowledge as K

from portal_api import db
from portal_api.auth import dashboard_guard, pool

router = APIRouter()


async def _team(p, name: str) -> dict[str, Any]:
    team = next((t for t in await db.list_teams(p) if t["name"] == name), None)
    if team is None:
        raise HTTPException(404, "no such team")
    return team


async def contents(p, team: str) -> dict[str, str]:
    return {r["section"]: r["content"] for r in await db.team_knowledge(p, team)}


def text_hash(section: str, text: str) -> str:
    return hashlib.sha256(f"{section}\n{' '.join(text.lower().split())}".encode()).hexdigest()[:24]


@router.get("/api/teams/{team}/knowledge")
async def get_knowledge(team: str, p=Depends(pool)):
    await _team(p, team)
    rows = {r["section"]: r for r in await db.team_knowledge(p, team)}
    now = datetime.now(UTC)
    return {"team": team, "sections": [
        {"key": s.key, "title": s.title, "help": s.help, "max_chars": s.max_chars, "always": s.always, "entries": s.entries,
         "content": (r := rows.get(s.key)) and r["content"] or "", "version": r["version"] if r else 0, "flags": r["flags"] if r else [],
         "updated_by": r["updated_by"] if r else None, "updated_at": r["updated_at"] if r else None,
         "confirmed_by": r["confirmed_by"] if r else None, "confirmed_at": r["confirmed_at"] if r else None,
         "stale": bool(r and r["content"].strip()) and K.stale(r["confirmed_at"], now)}
        for s in K.SECTIONS]}


class KnowledgeIn(BaseModel):
    content: str = Field(max_length=20_000)
    version: int = Field(ge=0)  # the version the editor started from
    by: str = "dashboard"


@router.put("/api/teams/{team}/knowledge/{section}", dependencies=[Depends(dashboard_guard)])
async def put_knowledge(team: str, section: str, body: KnowledgeIn, p=Depends(pool)):
    await _team(p, team)
    content = body.content.replace("\r\n", "\n").strip()
    if err := K.text_error(section, content):
        raise HTTPException(422, err)
    verdict = scan(content, "jira_comment")
    if verdict.verdict is Verdict.BLOCK:
        raise HTTPException(422, "refused: the text looks like an attempt to give the agent hidden instructions (" + ", ".join(f.rule_id for f in verdict.findings[:3]) + ")")
    if not check_outbound(content).ok:
        raise HTTPException(422, "refused: the text looks like it contains a secret")
    row = await db.save_team_knowledge(p, team=team, section=section, content=content, flags=[f.rule_id for f in verdict.findings], by=body.by,
                                       expected_version=body.version)
    if row is None:
        raise HTTPException(409, "someone saved this section while you were editing it; reload to see their version")
    return row


class By(BaseModel):
    by: str = "dashboard"


@router.post("/api/teams/{team}/knowledge/{section}/confirm", dependencies=[Depends(dashboard_guard)])
async def confirm_knowledge(team: str, section: str, who: By | None = None, p=Depends(pool)):
    """Someone checked the section and it is still true: it stops counting as stale for another 90 days."""
    if (row := await db.confirm_team_knowledge(p, team=team, section=section, by=(who or By()).by)) is None:
        raise HTTPException(404, "nothing written in this section yet")
    return row


@router.get("/api/teams/{team}/knowledge/{section}/history")
async def knowledge_history(team: str, section: str, p=Depends(pool)):
    return await db.team_knowledge_history(p, team=team, section=section)


@router.get("/api/teams/{team}/knowledge-preview")
async def preview(team: str, q: str = Query("", max_length=4000), p=Depends(pool)):
    """Exactly what an agent would get for a ticket with this text."""
    await _team(p, team)
    return {"markdown": K.for_task(team, await contents(p, team), q)}


@router.get("/api/teams/{team}/knowledge-suggestions")
async def suggestions(team: str, p=Depends(pool)):
    """What could be added: terms and systems found in the team's indexed docs, lessons learned across its repositories, and written rules
    reviewers keep raising. Nothing is added until a person accepts it."""
    t = await _team(p, team)
    text = await contents(p, team)
    chunks, readmes = await db.knowledge_sources(p, team, t["repos"])
    lessons = await db.list_lessons(p, status="approved", limit=2000)
    feedback = I.feedback_from_signals(await db.learning_signals(p, days=90, team=team))
    rules = K.lines(text.get("rules", ""))
    found = (I.glossary_from_docs(chunks, [n for n, _ in K.entries(text.get("glossary", ""))])
             + I.systems_from_readmes(t["repos"], readmes, [text.get("systems", "")])
             + I.promotions(lessons, team, t["repos"], rules)
             + I.rules_still_raised(rules, feedback, team))
    gone = await db.dismissed(p, team)
    return [{**s.model_dump(), "id": text_hash(s.section, s.text)} for s in found if (s.section, text_hash(s.section, s.text)) not in gone]


class Dismiss(BaseModel):
    section: str
    id: str
    by: str = "dashboard"


@router.post("/api/teams/{team}/knowledge-suggestions/dismiss", dependencies=[Depends(dashboard_guard)])
async def dismiss(team: str, body: Dismiss, p=Depends(pool)):
    await db.dismiss_suggestion(p, team=team, section=body.section, text_hash=body.id, by=body.by)
    return {"ok": True}
