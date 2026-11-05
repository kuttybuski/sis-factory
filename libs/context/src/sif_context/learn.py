"""Learning from history (pure): reusable answers, recurring review feedback, budget calibration and failure summaries."""

import math
import re
from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel

# ---- answered questions -> reusable knowledge ------------------------------------------------


class QA(BaseModel):
    task_id: str
    question: str
    answer: str
    answered_at: str = ""


_MENTION_PREFIX = re.compile(r"^\s*@\w[\w-]*[\s:,-]*")


def qa_pairs_from_events(task_id: str, events: list[dict[str, Any]]) -> list[QA]:
    """Pairs each agent question with the next human answer. Events are task_events payloads in order."""
    pairs, pending = [], None
    for e in events:
        t = e.get("type")
        if t == "agent.asked_question" and e.get("reason") != "budget" and e.get("text"):
            pending = e["text"]
        elif t == "agent.answered" and pending and e.get("text"):
            answer = _MENTION_PREFIX.sub("", e["text"]).strip()
            if answer:
                pairs.append(QA(task_id=task_id, question=pending.strip(), answer=answer, answered_at=str(e.get("ts", ""))))
            pending = None
        elif t in ("task.killed", "task.failed", "wait.timed_out"):
            pending = None
    return pairs


# ---- recurring review feedback -> candidate lessons -------------------------------------------

_CUE = re.compile(r"\b(always|never|should|must|prefer|avoid|don't|do not|instead of|we (use|prefer|always|never|don't)|please (use|add|remove|avoid|keep|make|rename|move)|needs? to)\b", re.IGNORECASE)
_STOP = {"the", "and", "for", "that", "this", "with", "you", "are", "not", "use", "can", "should", "please", "from", "have", "your", "its"}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]{3,}", s.lower()) if w not in _STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def candidate_sentences(body: str) -> list[str]:
    """Imperative or preference-like sentences from a review comment; not questions, not code, 25-300 characters."""
    body = re.sub(r"```.*?```", " ", body, flags=re.DOTALL)
    out = []
    for s in re.split(r"(?<=[.!?])\s+|\n+", body):
        s = s.strip(" -*>")
        if 25 <= len(s) <= 300 and not s.endswith("?") and _CUE.search(s) and "`" not in s[:2]:
            out.append(s)
    return out


class ReviewNote(BaseModel):
    pr: str
    reviewer: str
    body: str
    url: str = ""


class LessonCandidate(BaseModel):
    text: str
    count: int  # distinct PRs that raised it
    reviewers: list[str]
    evidence: list[str]  # PR links


def mine_review_lessons(notes: list[ReviewNote], min_count: int = 2, similarity: float = 0.55) -> list[LessonCandidate]:
    """Groups similar feedback across different PRs. One-off remarks are noise; the same remark on several PRs is a rule."""
    clusters: list[dict[str, Any]] = []
    for n in notes:
        for sent in candidate_sentences(n.body):
            w = _words(sent)
            for c in clusters:
                if _jaccard(w, c["words"]) >= similarity:
                    c["prs"].add(n.pr); c["reviewers"].add(n.reviewer); c["urls"].add(n.url or n.pr)
                    if len(sent) < len(c["text"]):
                        c["text"] = sent  # keep the most concise phrasing
                    break
            else:
                clusters.append({"text": sent, "words": w, "prs": {n.pr}, "reviewers": {n.reviewer}, "urls": {n.url or n.pr}})
    found = [LessonCandidate(text=c["text"], count=len(c["prs"]), reviewers=sorted(c["reviewers"]), evidence=sorted(c["urls"])[:5])
             for c in clusters if len(c["prs"]) >= min_count]
    return sorted(found, key=lambda x: -x.count)


TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")


def review_notes(prs: list[Any], trusted: tuple[str, ...] = TRUSTED) -> list[ReviewNote]:
    """Reviewer remarks from PRData objects: inline review comments and review/discussion comments (not the PR author's own). Only people
    in the organisation count (GitHub's author_association): on a public repository anyone can comment, and recurring remarks become
    candidate lessons. Bots never count."""
    notes = []
    ok = lambda c, pr: c.author and c.author != pr.author and not c.author.endswith("[bot]") and c.association.upper() in trusted  # noqa: E731
    for pr in prs:
        for rc in pr.review_comments:
            if ok(rc, pr):
                notes.append(ReviewNote(pr=f"{pr.repo}#{pr.number}", reviewer=rc.author, body=rc.body, url=pr.url))
        for c in pr.comments:
            if ok(c, pr):
                notes.append(ReviewNote(pr=f"{pr.repo}#{pr.number}", reviewer=c.author, body=c.body, url=pr.url))
    return notes


# ---- budgets from history ---------------------------------------------------------------------


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


class BudgetSuggestion(BaseModel):
    group: str
    samples: int
    success_rate: float
    p50_cost: float
    p90_cost: float
    suggested_budget: float
    current_budget: float
    note: str


def calibrate_budgets(rows: list[dict[str, Any]], current: float, min_samples: int = 8, headroom: float = 1.25) -> list[BudgetSuggestion]:
    """rows: {group, cost_usd, outcome}. Suggests the p90 cost of tasks that opened a PR, plus headroom."""
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["outcome"] not in (None, "bounced"):
            by[r["group"]].append(r)
    out = []
    for group, rs in sorted(by.items()):
        wins = [r["cost_usd"] for r in rs if r["outcome"] in ("pr_opened", "merged")]  # closed PRs were rejected work
        rate = len(wins) / len(rs)
        p50, p90 = percentile(wins, 0.5), percentile(wins, 0.9)
        if len(rs) < min_samples or not wins:
            out.append(BudgetSuggestion(group=group, samples=len(rs), success_rate=round(rate, 2), p50_cost=round(p50, 2), p90_cost=round(p90, 2),
                                        suggested_budget=current, current_budget=current, note=f"Need at least {min_samples} finished tasks (and a PR) before suggesting."))
            continue
        suggested = round(max(0.25, p90 * headroom), 2)
        note = ("Budget looks too high for the work done." if suggested < current * 0.6 else
                "Budget looks too tight; successful tasks often run near the cap." if suggested > current * 1.1 else "Budget fits.")
        out.append(BudgetSuggestion(group=group, samples=len(rs), success_rate=round(rate, 2), p50_cost=round(p50, 2), p90_cost=round(p90, 2),
                                    suggested_budget=suggested, current_budget=current, note=note))
    return out


# ---- failure summary --------------------------------------------------------------------------


class Retro(BaseModel):
    total: int
    by_outcome: dict[str, int]
    top_errors: list[tuple[str, int]]
    findings: list[str]


_ERR = re.compile(r"^(error|exception|traceback|fatal)\b[: ]*", re.IGNORECASE)
_VARIABLE = re.compile(r"\b\d+\b|0x[0-9a-f]+|[0-9a-f]{8,}", re.IGNORECASE)


def summarize_failures(tasks: list[dict[str, Any]], log_lines: list[str]) -> Retro:
    """tasks: {outcome}. log_lines: agent log lines from failed tasks. Counts and groups what went wrong."""
    outcomes = Counter(t["outcome"] for t in tasks if t.get("outcome"))
    errors = Counter(_VARIABLE.sub("N", _ERR.sub("", l.strip()))[:120] for l in log_lines if _ERR.match(l.strip()))
    total = sum(outcomes.values())
    findings = []
    if total:
        for outcome, advice in (("timed_out", "Replies are arriving too slowly or the wait limit is short: consider reminders or a longer SIF_WAIT_TIMEOUT_MINUTES."),
                                ("bounced", "Many tickets lack detail: add a ticket template or acceptance-criteria checklist."),
                                ("failed", "Agent runs are failing: look at the top errors below and consider a lesson or environment fix."),
                                ("killed", "Runs are being stopped by hand: check budgets and whether tasks are scoped too widely.")):
            share = outcomes.get(outcome, 0) / total
            if total >= 5 and share >= 0.2:
                findings.append(f"{round(share * 100)}% of tasks ended '{outcome}'. {advice}")
    return Retro(total=total, by_outcome=dict(outcomes), top_errors=errors.most_common(5), findings=findings)


# ---- eval gate --------------------------------------------------------------------------------


class EvalReport(BaseModel):
    n: int
    baseline_pass: int
    candidate_pass: int


def passes_gate(r: EvalReport, min_n: int = 10, max_regression: int = 0) -> bool:
    """A change may go live only if it was replayed on enough past tasks and did not make results worse."""
    return r.n >= min_n and r.candidate_pass >= r.baseline_pass - max_regression
