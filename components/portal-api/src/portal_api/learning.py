"""Learning: lessons ("@sis remember ..."), the replay benchmark that judges them, the daily knowledge PR, the self-improvement cycle,
and what history says about failures and budgets."""

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from injection_guard import Verdict, check_outbound, scan
from sis_contracts import knowledge as K
from sis_contracts import lessons as L
from sis_context.learn import EvalReport, calibrate_budgets, passes_gate, summarize_failures

from portal_api import db, improve, knowledge, settings
from portal_api.auth import dashboard_guard, pool, write_guard

router = APIRouter()


class LessonIn(BaseModel):
    text: str
    scope: L.Scope = L.Scope.REPO
    scope_value: str | None = None
    repo: str | None = None
    source: L.Source = L.Source.HUMAN_COMMENT
    author: str
    author_role: L.Role = L.Role.MEMBER  # resolved by the caller (jira-connector) from its allowlists
    ticket_key: str | None = None
    source_url: str | None = None
    ttl_days: int | None = None


@router.post("/api/lessons", status_code=201, dependencies=[Depends(write_guard)])
async def create_lesson(body: LessonIn, p=Depends(pool)):
    return await admit_lesson(p, body)


async def admit_lesson(p, body: LessonIn) -> dict[str, Any]:
    """Every lesson enters here, whoever proposes it: a person in Jira, context-ingest, or the improvement cycle."""
    raw = body.text
    if len(raw) > 4 * settings.lesson_max_chars:  # bound the work before scanning
        raise HTTPException(422, f"A lesson can be at most {settings.lesson_max_chars} characters; split it into separate rules.")
    text = L.clean_text(raw)
    if err := L.text_error(text, settings):
        raise HTTPException(422, err)
    scope_value = body.scope_value or (body.repo if body.scope is L.Scope.REPO else None)
    if body.scope is not L.Scope.GLOBAL and not scope_value:
        raise HTTPException(422, f"scope '{body.scope}' needs a scope_value (repo name, team name or path glob)")
    repo = body.repo or (scope_value if body.scope is L.Scope.REPO else None)
    if body.scope is L.Scope.PATH and not repo:
        raise HTTPException(422, "path scope needs the repo it applies to")

    if dup := await db.find_duplicate_lesson(p, text=text, scope=body.scope.value, scope_value=scope_value, repo=repo):
        return {**dup, "duplicate": True}

    # Lessons are the one place text may act as an instruction, so they get the strictest checks.
    status, reason, flags = None, None, []
    # Scan the RAW text: cleaning strips pipes, backticks and tags, which would hide exactly what the scan looks for.
    verdict = scan(raw, "jira_comment")
    flags = [f.rule_id for f in verdict.findings]
    if verdict.verdict is Verdict.BLOCK:
        status, reason = "rejected", "blocked by injection-guard: " + ", ".join(flags)
    elif not check_outbound(raw).ok:
        status, reason = "rejected", "looks like it contains a secret"
    else:
        decision = L.decide(body.scope, body.author_role, body.source, settings, flagged=verdict.verdict is not Verdict.ALLOW)
        if decision is L.Decision.DENIED:
            status, reason = "rejected", "author is not allowed to add lessons"
        else:
            status = decision.value
            reason = "flagged by injection-guard, needs review" if verdict.verdict is Verdict.FLAG else None
    row = await db.create_lesson(
        p, text=text, scope=body.scope.value, scope_value=scope_value, repo=repo, status=status, source=body.source.value,
        author=body.author, author_role=body.author_role.value, ticket_key=body.ticket_key, source_url=body.source_url,
        flags=flags, reason=reason, ttl_days=body.ttl_days or settings.lesson_default_ttl_days)
    return {**row, "duplicate": False}


@router.get("/api/lessons")
async def lessons(status: str | None = None, p=Depends(pool)):
    return await db.list_lessons(p, status=status)


class Actor(BaseModel):
    by: str = "dashboard"
    reason: str | None = None


def _moved(row):
    if row is None:
        raise HTTPException(409, "lesson not found or not in a state that allows this")
    return row


# The dashboard calls these. Like /kill, they are open until oauth2-proxy and per-role checks sit in front of the portal.
@router.post("/api/lessons/{lesson_id}/approve", dependencies=[Depends(dashboard_guard)])
async def approve_lesson(lesson_id: int, who: Actor | None = None, p=Depends(pool)):
    lesson = await db.get_lesson(p, lesson_id)
    if lesson and lesson["scope"] == "global" and settings.lesson_global_requires_eval and lesson["eval_status"] != "passed":
        raise HTTPException(409, "global lessons need a passed eval report first (SIS_LESSON_GLOBAL_REQUIRES_EVAL)")
    return _moved(await db.transition_lesson(p, lesson_id, to="approved", allowed_from=("candidate",), by=(who or Actor()).by))


@router.post("/api/lessons/{lesson_id}/reject", dependencies=[Depends(dashboard_guard)])
async def reject_lesson(lesson_id: int, who: Actor | None = None, p=Depends(pool)):
    w = who or Actor()
    return _moved(await db.transition_lesson(p, lesson_id, to="rejected", allowed_from=("candidate",), by=w.by, reason=w.reason or "rejected on review"))


@router.post("/api/lessons/{lesson_id}/disable", dependencies=[Depends(write_guard)])
async def disable_lesson(lesson_id: int, who: Actor | None = None, p=Depends(pool)):
    w = who or Actor()
    return _moved(await db.transition_lesson(p, lesson_id, to="disabled", allowed_from=("approved", "candidate"), by=w.by, reason=w.reason or f"disabled by {w.by}"))


@router.get("/api/lessons/relevant")
async def relevant_lessons(repo: str | None = None, team: str | None = None, paths: str = "", q: str = "", record_use: bool = False,
                           task_id: str | None = None, include: int | None = None, exclude: int | None = None, p=Depends(pool)):
    """Approved, in-scope lessons for a task, ready to place in the agent's trusted rules block. With a task_id, machine-proposed lessons are
    withheld from a random share of tasks (SIS_LESSON_HOLDOUT_PCT) and both groups are recorded, so the improvement cycle can measure them.
    include / exclude are for replays: the lesson under test is forced in (even as a candidate) or kept out."""
    rows = await db.approved_lessons(p)
    fields = ("id", "text", "scope", "scope_value", "repo", "status", "created_at", "expires_at", "use_count", "source")
    pool_ = [L.Lesson(**{k: r[k] for k in fields}) for r in rows if r["id"] not in (include, exclude)]
    ctx = L.TaskContext(repo=repo, team=team, paths=[x for x in paths.split(",") if x], query=q)
    chosen = L.select_lessons(pool_, ctx, datetime.now(UTC), settings)
    if include is not None and (forced := await db.get_lesson(p, include)) and forced["status"] in ("candidate", "approved"):
        chosen = [L.Lesson(**{**{k: forced[k] for k in fields}, "status": "approved"}), *chosen][:max(settings.lesson_max_in_prompt, 1)]
    held = [l for l in chosen if task_id and L.held_out(task_id, l, settings.lesson_holdout_pct)]
    chosen = [l for l in chosen if l not in held]
    if record_use:
        await db.record_lesson_use(p, [l.id for l in chosen])
    if record_use and task_id:
        await db.record_lesson_uses(p, task_id, [l.id for l in chosen], [l.id for l in held])
    team_md = K.for_task(team, await knowledge.contents(p, team), q) if team else ""  # what the team wrote down, the parts this ticket needs
    return {"lessons": [{"id": l.id, "text": l.text, "scope": l.scope} for l in chosen], "held_out": [l.id for l in held], "rules": L.render_rules(chosen),
            "knowledge": team_md}


class EvalIn(BaseModel):
    n: int
    baseline_pass: int
    candidate_pass: int


@router.post("/api/lessons/{lesson_id}/eval", dependencies=[Depends(write_guard)])
async def record_eval(lesson_id: int, report: EvalIn, p=Depends(pool)):
    """Called by the eval harness after replaying past tasks with and without the lesson."""
    ok = passes_gate(EvalReport(**report.model_dump()))
    return _moved(await db.set_lesson_eval(p, lesson_id, status="passed" if ok else "failed", n=report.n, baseline=report.baseline_pass, candidate=report.candidate_pass))


# ---------------------------------------------------------------- replay benchmark
class CaseIn(BaseModel):
    task_id: str
    repo: str
    team: str | None = None
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    title: str
    description: str = ""
    reference: str


@router.post("/api/eval/cases", status_code=201, dependencies=[Depends(write_guard)])
async def add_eval_case(body: CaseIn, p=Depends(pool)):
    """orchestrator-worker adds one when a factory PR merges. An empty reference (nothing changed) cannot score anything, so it is refused."""
    if not body.reference.strip():
        raise HTTPException(422, "the reference diff is empty")
    await db.put_eval_case(p, **body.model_dump())
    return {"ok": True}


@router.get("/api/eval/cases")
async def eval_cases(p=Depends(pool)):
    return await db.eval_case_counts(p)


async def request_replay(p, lesson_id: int, by: str) -> dict[str, Any]:
    lesson = await db.get_lesson(p, lesson_id)
    if not lesson or lesson["status"] not in ("candidate", "approved"):
        raise HTTPException(409, "only candidate and approved lessons can be replayed")
    cases = await db.eval_cases_for(p, lesson, settings.eval_max_cases)
    if not cases:
        raise HTTPException(409, "no merged tickets to replay for this lesson's scope yet: cases are kept as the factory's PRs merge")
    run = await db.request_eval_run(p, lesson_id=lesson_id, by=by, case_ids=[c["task_id"] for c in cases], budget_usd=settings.eval_budget_usd,
                                    pass_score=settings.eval_pass_score)
    if run is None:
        raise HTTPException(409, "a replay of this lesson is already waiting or running")
    return {**run, "max_cost_usd": round(2 * len(cases) * settings.eval_budget_usd, 2)}


@router.post("/api/lessons/{lesson_id}/replay", status_code=201, dependencies=[Depends(dashboard_guard)])
async def replay_lesson(lesson_id: int, who: Actor | None = None, p=Depends(pool)):
    """Replays recent merged tickets in the lesson's scope twice, with and without it. Costs up to 2 x cases x SIS_EVAL_BUDGET_USD."""
    return await request_replay(p, lesson_id, (who or Actor()).by)


@router.post("/api/eval/runs/claim", dependencies=[Depends(write_guard)])
async def claim_eval_runs(p=Depends(pool)):
    return await db.claim_eval_runs(p)


class EvalResultIn(BaseModel):
    n: int = 0
    baseline_pass: int = 0
    candidate_pass: int = 0
    cost_usd: float = 0.0
    cases: list[dict[str, Any]] = []
    error: str | None = None


@router.post("/api/eval/runs/{run_id}/result", dependencies=[Depends(write_guard)])
async def eval_run_result(run_id: int, body: EvalResultIn, p=Depends(pool)):
    """EvalWorkflow reports here. A finished run becomes the lesson's eval report, through the same gate as any other."""
    ok = body.error is None
    run = await db.finish_eval_run(p, run_id, ok=ok, result=body.model_dump())
    if run is None:
        raise HTTPException(409, "replay run not found or already finished")
    if ok and body.n:
        passed = passes_gate(EvalReport(n=body.n, baseline_pass=body.baseline_pass, candidate_pass=body.candidate_pass))
        await db.set_lesson_eval(p, run["lesson_id"], status="passed" if passed else "failed", n=body.n, baseline=body.baseline_pass, candidate=body.candidate_pass)
    return run


@router.get("/api/eval/runs")
async def eval_runs(lesson_id: int | None = None, p=Depends(pool)):
    return await db.list_eval_runs(p, lesson_id=lesson_id)


# ---------------------------------------------------------------- the daily knowledge PR
@router.post("/api/digests/claim", dependencies=[Depends(write_guard)])
async def claim_digests(p=Depends(pool)):
    """orchestrator-worker asks which repositories are due a knowledge PR (SIS_DIGEST_HOURS) and opens them."""
    return [] if settings.digest_hours <= 0 else await db.claim_digests(p, every_hours=settings.digest_hours)


class DigestOpenedIn(BaseModel):
    pr_number: int
    pr_url: str
    branch: str


@router.post("/api/digests/{digest_id}/opened", dependencies=[Depends(write_guard)])
async def digest_opened(digest_id: int, body: DigestOpenedIn, p=Depends(pool)):
    if (row := await db.digest_opened(p, digest_id, **body.model_dump())) is None:
        raise HTTPException(409, "knowledge PR not found or not being opened")
    return row


class DigestDecidedIn(BaseModel):
    state: Literal["merged", "closed", "failed"]
    merged_by: str | None = None
    kept_ids: list[int] = []
    error: str | None = None


@router.post("/api/digests/{digest_id}/decided", dependencies=[Depends(write_guard)])
async def digest_decided(digest_id: int, body: DigestDecidedIn, p=Depends(pool)):
    """Merging the PR is the approval: what is still in the block is adopted, what a reviewer deleted is rejected."""
    if (row := await db.decide_digest(p, digest_id, state=body.state, merged_by=body.merged_by, kept=body.kept_ids, error=body.error)) is None:
        raise HTTPException(409, "knowledge PR not found or already decided")
    return row


@router.get("/api/digests")
async def digests(status: str | None = None, p=Depends(pool)):
    return await db.list_digests(p, status=status)


# ---------------------------------------------------------------- self-improvement
async def run_improvement(p, trigger: str) -> dict[str, Any]:
    async def propose(**kw) -> dict[str, Any] | None:
        try:
            return await admit_lesson(p, LessonIn(**kw))
        except HTTPException:
            return None  # too long, wrong shape: a mined sentence that would not make a rule

    async def replay(lesson_id: int) -> dict[str, Any] | None:
        try:
            return await request_replay(p, lesson_id, "sis-improve")
        except HTTPException:
            return None  # no cases yet, or already running

    return await improve.run_cycle(p, settings, propose=propose, replay=replay if settings.eval_auto else None, trigger=trigger)


async def improve_forever(p) -> None:
    """The cycle on a timer (SIS_IMPROVE_MINUTES). It only proposes lessons and retires machine-proposed ones that measurably hurt;
    everything else it finds is a proposal on the Learning page for a person to act on."""
    if settings.improve_minutes <= 0:
        return
    while True:
        await asyncio.sleep(settings.improve_minutes * 60)
        try:
            await run_improvement(p, "schedule")
        except Exception as e:  # noqa: BLE001 - try again next time; the cycle must never take the API down
            print(f"improvement cycle failed: {type(e).__name__}: {e}", flush=True)


@router.post("/api/improve/run", dependencies=[Depends(dashboard_guard)])
async def improve_now(p=Depends(pool)):
    return await run_improvement(p, "manual")


@router.get("/api/improve")
async def improvement(team: str | None = None, p=Depends(pool)):
    """The latest cycle's report, earlier runs, and live measurements of the lessons in use."""
    return await improve.status(p, settings, team=team)


# ---------------------------------------------------------------- learning from history
@router.get("/api/retro")
async def retro(days: int = Query(7, ge=1, le=365), team: str | None = None, include_demo: bool = False, p=Depends(pool)):
    tasks, lines = await db.outcomes_and_errors(p, days=days, include_demo=include_demo, team=team)
    return {"days": days, **summarize_failures(tasks, lines).model_dump()}


@router.get("/api/calibration")
async def calibration(group: str = Query("team", pattern="^(team|repo|profile)$"), days: int = Query(90, ge=7, le=365), team: str | None = None,
                      include_demo: bool = False, p=Depends(pool)):
    """Budget suggestions from what successful tasks actually cost."""
    rows = await db.finished_task_costs(p, group=group, days=days, include_demo=include_demo, team=team)
    return [s.model_dump() for s in calibrate_budgets(rows, current=settings.max_budget_usd)]
