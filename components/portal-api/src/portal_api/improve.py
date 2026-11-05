"""One run of the self-improvement cycle: read what happened to the factory's work, propose lessons, measure the lessons in use, retire
machine-proposed lessons that measurably hurt, and write down proposals for people. The analysis is pure (sis_context.improve); this
module only reads and writes.

What it may change on its own is deliberately small: it can add *candidate* lessons (a person approves them), and it can disable an approved
lesson that no person wrote once the comparison with tasks that did not get it shows it makes outcomes worse. Everything else is a proposal."""

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from sis_context import improve as I
from sis_context.learn import EvalReport, mine_review_lessons, passes_gate
from sis_contracts import Settings
from sis_contracts import lessons as L

from portal_api import db

BOT = "sis-improve"


async def run_cycle(p, s: Settings, *, propose: Callable[..., Awaitable[dict[str, Any] | None]], trigger: str,
                    replay: Callable[[int], Awaitable[dict[str, Any] | None]] | None = None) -> dict[str, Any]:
    """replay (SIS_EVAL_AUTO): asks for a replay of every lesson this run proposes, so it arrives on the Learning page already judged."""
    signals = await db.learning_signals(p, days=s.improve_days)
    feedback = I.feedback_from_signals(signals)
    lessons = await db.list_lessons(p, status=None, limit=5000)
    known = [l["text"] for l in lessons]  # rejected and disabled ones too, so a rule people turned down is not proposed again

    proposed: list[dict[str, Any]] = []

    async def offer(text: str, repo: str, author: str, why: str) -> None:
        if not I.novel(text, known):
            return
        row = await propose(text=text, scope="repo", repo=repo, source="mined", author=author, author_role="member")
        if row and not row.get("duplicate"):
            known.append(row["text"])
            proposed.append({"id": row["id"], "text": row["text"], "repo": repo, "status": row["status"], "why": why})

    for repo, notes in I.review_notes_by_repo(feedback).items():
        for cand in mine_review_lessons(notes, min_count=2):
            await offer(cand.text, repo, cand.reviewers[0], f"asked for on {cand.count} of the factory's PRs")
    for c in I.ci_candidates(feedback):
        await offer(c.text, c.repo, "CI", f"'{c.check}' failed on {len(c.tasks)} of the factory's PRs")

    replays = []
    if replay:
        for x in proposed:
            if run := await replay(x["id"]):
                replays.append({"lesson_id": x["id"], "run_id": run["id"], "cases": len(run["case_ids"])})

    # Measure the lessons in use, from the two random groups recorded when context packs were built.
    outcomes = await db.lesson_outcomes(p, days=s.improve_days * 3)
    effects = I.lesson_effects(outcomes, min_n=s.improve_min_tasks)
    by_id = {l["id"]: l for l in lessons}
    retired, evaluated = [], []
    for e in effects:
        l = by_id.get(e.lesson_id)
        if not l or l["status"] != "approved" or e.verdict == "collecting":
            continue
        n, base, cand = I.eval_counts(e)
        status = "passed" if passes_gate(EvalReport(n=n, baseline_pass=base, candidate_pass=cand)) else "failed"
        if l["eval_status"] != status or l["eval_n"] != n:
            await db.set_lesson_eval(p, e.lesson_id, status=status, n=n, baseline=base, candidate=cand)
            evaluated.append({"id": e.lesson_id, "eval": status, "n": n})
        if e.verdict == "hurting" and l["source"] in {x.value for x in L.MACHINE} and await db.transition_lesson(
                p, e.lesson_id, to="disabled", allowed_from=("approved",), by=BOT, reason=f"retired automatically: {e.note}"):
            retired.append({"id": e.lesson_id, "text": l["text"], "why": e.note})

    applied: dict[int, set[str]] = defaultdict(set)
    for r in outcomes:
        if r["applied"]:
            applied[r["lesson_id"]].add(r["task_id"])
    live = [l for l in lessons if l["status"] == "approved" and l["id"] not in {r["id"] for r in retired}]
    recurrences = I.recurring_despite(live, feedback, applied)
    tasks = await db.finished_tasks(p, days=s.improve_days)
    quality = I.repo_quality(tasks, s.improve_min_tasks)
    waiting = sum(1 for l in lessons if l["status"] == "candidate") + sum(1 for x in proposed if x["status"] == "candidate")
    props = I.proposals(gaps=I.intake_gaps(signals), recurrences=recurrences, effects=effects, lessons_by_id=by_id, quality_rows=quality,
                        feedback=feedback, waiting=waiting, min_n=s.improve_min_tasks)
    report = {
        "days": s.improve_days,
        "signals": {"review_items": sum(f.kind == "review" for f in feedback), "ci_failures": sum(f.kind == "ci" for f in feedback),
                    "fixups": sum(f.kind == "fixup" for f in feedback), "tasks": len(tasks)},
        "proposed_lessons": proposed,
        "replays_requested": replays,
        "retired_lessons": retired,
        "evaluated_lessons": evaluated,
        "proposals": [x.model_dump() for x in props],
    }
    run = await db.record_improvement_run(p, trigger=trigger, report=report)
    return {"id": run["id"], "ran_at": run["ran_at"], "trigger": trigger, **report}


async def status(p, s: Settings, *, team: str | None = None) -> dict[str, Any]:
    runs = await db.list_improvement_runs(p)
    outcomes = await db.lesson_outcomes(p, days=s.improve_days * 3)
    tasks = await db.finished_tasks(p, days=s.improve_days, team=team)
    return {
        "latest": runs[0] if runs else None,
        "history": [{"id": r["id"], "ran_at": r["ran_at"], "trigger": r["trigger"], "proposed": len(r["report"].get("proposed_lessons", [])),
                     "retired": len(r["report"].get("retired_lessons", [])), "proposals": len(r["report"].get("proposals", []))} for r in runs],
        "effects": [e.model_dump() for e in I.lesson_effects(outcomes, min_n=s.improve_min_tasks)],
        "quality": I.repo_quality(tasks, s.improve_min_tasks),
        "holdout_pct": s.lesson_holdout_pct,
        "every_minutes": s.improve_minutes,
        "digest_hours": s.digest_hours,
        "digest_file": s.digest_file,
    }
