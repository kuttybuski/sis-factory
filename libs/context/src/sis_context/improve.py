"""The self-improvement cycle (pure): what the factory learns from what happened to its own work, and how it checks that what it learned helps.

Signals come from the task event log: reviewers sending a PR back (review.received), CI failing on it, people adding commits before merging
(pr.merged fixups), clarifying questions and bounces at intake. They become:

- candidate lessons (recurring review feedback, recurring CI failures), which a person approves like any other lesson;
- measurements of the lessons already in use: machine-proposed lessons are withheld from a random share of tasks (lessons.held_out), so
  their effect is a comparison between two random groups, not a guess; a lesson that makes outcomes worse is retired;
- proposals for changing the factory itself (ticket templates, rules that are not being followed, lessons that do nothing), which people act on.
"""

import math
import re
from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel

from sis_context.learn import ReviewNote, _jaccard, _words

# ---- reading the event log --------------------------------------------------------------------

# review.received text is what the agent was asked, one block per item: "Review from ann (src/a.py):\n…" or "CI:\n<check> failed. <summary>".
_BLOCK = re.compile(r"(?m)^(CI|Review from (?P<who>[^\n(]+?)(?: \((?P<path>[^\n)]*)\))?):\n")
_CI_CHECK = re.compile(r"(?m)^(.{1,120}?) failed\.")
_FIXUP = re.compile(r"(?m)^- (.+) \(([^()\n]*)\)$")  # pr.merged text: "- <commit message> (<author>)"


class Feedback(BaseModel):
    task_id: str
    repo: str | None
    team: str | None
    kind: str  # review | ci | fixup
    author: str
    body: str
    path: str = ""
    ts: Any = None


def parse_review_text(text: str) -> list[tuple[str, str, str, str]]:
    """(kind, author, path, body) for each item in a review.received text."""
    parts = _BLOCK.split(text or "")  # [prefix, whole, who, path, body, whole, who, path, body, ...]
    out = []
    for i in range(1, len(parts) - 3, 4):
        whole, who, path, body = parts[i], parts[i + 1], parts[i + 2], parts[i + 3].strip()
        if whole == "CI":
            out.append(("ci", "CI", "", body))
        elif body:
            out.append(("review", (who or "").strip(), (path or "").strip(), body))
    return out


def feedback_from_signals(rows: list[dict[str, Any]]) -> list[Feedback]:
    """rows: {task_id, repo, team, type, ts, payload}. Review and CI items from revision rounds, and the commit messages of human fixups."""
    out = []
    for r in rows:
        p = r.get("payload") or {}
        if r["type"] == "review.received":
            for kind, author, path, body in parse_review_text(p.get("text", "")):
                out.append(Feedback(task_id=r["task_id"], repo=r.get("repo"), team=r.get("team"), kind=kind, author=author, body=body, path=path, ts=r.get("ts")))
        elif r["type"] == "pr.merged" and p.get("fixups") and p.get("text"):
            for m in _FIXUP.finditer(p["text"]):
                out.append(Feedback(task_id=r["task_id"], repo=r.get("repo"), team=r.get("team"), kind="fixup", author=m.group(2), body=m.group(1), ts=r.get("ts")))
    return out


def review_notes_by_repo(feedback: list[Feedback]) -> dict[str, list[ReviewNote]]:
    """Reviewer remarks and fixup messages as ReviewNotes, one task = one 'PR', ready for learn.mine_review_lessons."""
    by: dict[str, list[ReviewNote]] = defaultdict(list)
    for f in feedback:
        if f.kind in ("review", "fixup") and f.repo:
            by[f.repo].append(ReviewNote(pr=f.task_id, reviewer=f.author or "reviewer", body=f.body))
    return by


class CICandidate(BaseModel):
    repo: str
    check: str
    tasks: list[str]

    @property
    def text(self) -> str:
        return (f"CI check '{self.check}' has failed on the factory's pull requests in this repository: run it (or the commands it runs) "
                "before you finish, and fix what it reports.")


def ci_candidates(feedback: list[Feedback], min_count: int = 2) -> list[CICandidate]:
    """A check that fails on the factory's PRs again and again is something the agent should run before it finishes."""
    by: dict[tuple[str, str], set[str]] = defaultdict(set)
    for f in feedback:
        if f.kind == "ci" and f.repo:
            for check in {c.strip() for c in _CI_CHECK.findall(f.body)}:
                by[(f.repo, check)].add(f.task_id)
    found = [CICandidate(repo=repo, check=check, tasks=sorted(ts)) for (repo, check), ts in by.items() if len(ts) >= min_count]
    return sorted(found, key=lambda c: (-len(c.tasks), c.repo, c.check))


def novel(text: str, existing: list[str], similarity: float = 0.55) -> bool:
    """Not a near-duplicate of a lesson already proposed, approved or rejected."""
    w = _words(text)
    return all(_jaccard(w, _words(e)) < similarity for e in existing)


# ---- measuring lessons ------------------------------------------------------------------------


def quality(outcome: str | None, review_rounds: int | None, fixups: int | None) -> float:
    """One number per finished task: 1 merged as delivered, 0.5 merged after revisions or human commits, 0 not merged."""
    if outcome != "merged":
        return 0.0
    return 1.0 if not review_rounds and not fixups else 0.5


class Effect(BaseModel):
    lesson_id: int
    applied: int  # finished tasks that got the lesson
    held: int  # finished tasks that were eligible but did not (the comparison group)
    merge_rate_applied: float
    merge_rate_held: float
    quality_applied: float
    quality_held: float
    verdict: str  # collecting | helping | hurting | no_effect
    note: str


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _var(xs: list[float]) -> float:
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1) if len(xs) > 1 else 0.0


def lesson_effects(rows: list[dict[str, Any]], min_n: int = 10, min_delta: float = 0.1, z: float = 1.64) -> list[Effect]:
    """rows: {lesson_id, applied, outcome, review_rounds, human_fixups}. Compares the mean task quality of the two random groups.
    A difference counts only if it is at least min_delta and larger than z standard errors, so a lucky streak does not retire a good rule."""
    groups: dict[int, dict[bool, list[dict[str, Any]]]] = defaultdict(lambda: {True: [], False: []})
    for r in rows:
        groups[r["lesson_id"]][bool(r["applied"])].append(r)
    out = []
    for lid, g in sorted(groups.items()):
        qa = [quality(r["outcome"], r["review_rounds"], r["human_fixups"]) for r in g[True]]
        qh = [quality(r["outcome"], r["review_rounds"], r["human_fixups"]) for r in g[False]]
        ma = _mean([1.0 if r["outcome"] == "merged" else 0.0 for r in g[True]])
        mh = _mean([1.0 if r["outcome"] == "merged" else 0.0 for r in g[False]])
        diff = _mean(qa) - _mean(qh)
        if len(qa) < min_n or len(qh) < min_n:
            verdict, note = "collecting", f"Needs {min_n} finished tasks with and without it ({len(qa)} with, {len(qh)} without so far)."
        else:
            se = math.sqrt(_var(qa) / len(qa) + _var(qh) / len(qh))
            bar = max(min_delta, z * se)
            if diff <= -bar:
                verdict, note = "hurting", f"Tasks with it did worse: quality {_mean(qa):.2f} vs {_mean(qh):.2f} without, merged {ma:.0%} vs {mh:.0%}."
            elif diff >= bar:
                verdict, note = "helping", f"Tasks with it did better: quality {_mean(qa):.2f} vs {_mean(qh):.2f} without, merged {ma:.0%} vs {mh:.0%}."
            else:
                verdict, note = "no_effect", f"No measurable difference yet (quality {_mean(qa):.2f} vs {_mean(qh):.2f})."
        out.append(Effect(lesson_id=lid, applied=len(qa), held=len(qh), merge_rate_applied=round(ma, 3), merge_rate_held=round(mh, 3),
                          quality_applied=round(_mean(qa), 3), quality_held=round(_mean(qh), 3), verdict=verdict, note=note))
    return out


def eval_counts(e: Effect) -> tuple[int, int, int]:
    """The online comparison expressed as an eval report (n, baseline_pass, candidate_pass) on equal-sized groups, for learn.passes_gate."""
    n = min(e.applied, e.held)
    return n, round(e.merge_rate_held * n), round(e.merge_rate_applied * n)


class Recurrence(BaseModel):
    lesson_id: int
    tasks: list[str]
    example: str


def recurring_despite(lessons: list[dict[str, Any]], feedback: list[Feedback], applied: dict[int, set[str]], similarity: float = 0.4,
                      min_count: int = 2) -> list[Recurrence]:
    """Approved lessons whose point reviewers still had to make, on tasks that were given the lesson: the agent is not following it,
    or it is worded so it cannot be followed."""
    out = []
    for l in lessons:
        lw, seen, example = _words(l["text"]), set(), ""
        for f in feedback:
            if f.kind == "review" and f.task_id in applied.get(l["id"], set()) and _jaccard(lw, _words(f.body)) >= similarity:
                seen.add(f.task_id)
                example = example or f.body[:300]
        if len(seen) >= min_count:
            out.append(Recurrence(lesson_id=l["id"], tasks=sorted(seen), example=example))
    return out


# ---- intake -----------------------------------------------------------------------------------

_ASK = re.compile(r"^Before I start, please tell me (.+?)\.?$", re.DOTALL)
_BOUNCE = re.compile(r"^Ticket needs more detail: (.+)$", re.DOTALL)


def intake_gaps(rows: list[dict[str, Any]]) -> list[tuple[str, str | None, int]]:
    """(missing detail, team, tasks) from clarifying questions and bounces: what tickets keep leaving out."""
    per: dict[tuple[str, str | None], set[str]] = defaultdict(set)
    for r in rows:
        text = ((r.get("payload") or {}).get("text") or "").strip()
        m = None
        if r["type"] == "agent.asked_question" and (r.get("payload") or {}).get("reason") == "question":
            m = _ASK.match(text)
            items = m.group(1).split(" and ") if m else []
        elif r["type"] == "jira.bounced":
            m = _BOUNCE.match(text)
            items = m.group(1).split("; ") if m else []
        else:
            items = []
        for it in items:
            if it.strip():
                per[(it.strip(), r.get("team"))].add(r["task_id"])
    return sorted(((d, t, len(ts)) for (d, t), ts in per.items()), key=lambda x: -x[2])


# ---- proposals for the factory itself ---------------------------------------------------------


class Proposal(BaseModel):
    kind: str
    title: str
    detail: str
    action: str
    evidence: list[str] = []


def repo_quality(tasks: list[dict[str, Any]], min_n: int = 10) -> list[dict[str, Any]]:
    """Per repository: of the factory's merged PRs, how many went in as delivered (no revision round, no human commits)."""
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in tasks:
        if t.get("repo") and t["outcome"] in ("merged", "closed"):
            by[t["repo"]].append(t)
    out = []
    for repo, ts in sorted(by.items()):
        merged = [t for t in ts if t["outcome"] == "merged"]
        clean = [t for t in merged if not t.get("review_rounds") and not t.get("human_fixups")]
        out.append({"repo": repo, "decided": len(ts), "merged": len(merged), "as_delivered": len(clean),
                    "first_time_right": round(len(clean) / len(ts), 3) if ts else 0.0, "enough": len(ts) >= min_n})
    return out


def proposals(*, gaps: list[tuple[str, str | None, int]], recurrences: list[Recurrence], effects: list[Effect], lessons_by_id: dict[int, dict[str, Any]],
              quality_rows: list[dict[str, Any]], feedback: list[Feedback], waiting: int, min_n: int = 10) -> list[Proposal]:
    out: list[Proposal] = []
    for detail, team, n in gaps:
        if n >= 3:
            out.append(Proposal(kind="intake", title=f"Tickets{f' from {team}' if team else ''} often leave out {detail}",
                                detail=f"{n} tickets needed a question or were sent back for it.",
                                action=f"Add “{detail}” to the Jira issue template{f' for {team}' if team else ''}, or to the handling instructions."))
    for r in recurrences:
        l = lessons_by_id.get(r.lesson_id, {})
        out.append(Proposal(kind="not_followed", title=f"L{r.lesson_id} is not being followed",
                            detail=f"Reviewers still asked for it on {len(r.tasks)} tasks that were given it, e.g. “{r.example}”",
                            action="Reword it as one concrete instruction, or enforce it with a linter or CI check so the agent sees it fail.",
                            evidence=[f"L{r.lesson_id}: {l.get('text', '')}", *r.tasks[:5]]))
    for e in effects:
        if e.verdict == "no_effect" and min(e.applied, e.held) >= 3 * min_n:
            out.append(Proposal(kind="no_effect", title=f"L{e.lesson_id} makes no measurable difference",
                                detail=e.note, action="Disable it: every rule in the prompt costs attention and tokens.",
                                evidence=[lessons_by_id.get(e.lesson_id, {}).get("text", "")]))
    top = Counter(f.path.split("/")[0] for f in feedback if f.kind == "review" and f.path)
    for q in quality_rows:
        if q["enough"] and q["first_time_right"] < 0.5:
            hot = ", ".join(p for p, _ in top.most_common(3))
            out.append(Proposal(kind="quality", title=f"Most PRs in {q['repo']} need rework before they merge",
                                detail=f"{q['as_delivered']} of {q['decided']} went in as delivered.{f' Reviewers comment most on {hot}.' if hot else ''}",
                                action="Write what reviewers keep asking for into the repository's CLAUDE.md, and approve the waiting candidate lessons."))
    if waiting:
        out.append(Proposal(kind="review_candidates", title=f"{waiting} lesson{'s' if waiting != 1 else ''} waiting for review",
                            detail="Mined from review feedback, CI failures and human fixups. None reaches the agent until someone approves it.",
                            action="Repository lessons go into that repository's next daily knowledge PR, where merging adopts them; others are approved "
                                   "below. Approved machine lessons are measured and retired if they do not help."))
    return out


# ---- team knowledge: drafting it from what is indexed, and keeping it honest with what is learned ---------------------------------

_DEF = re.compile(r"^\s*[-*•]?\s*\**([A-Z][\w /&-]{1,40}?)\**\s*[:—–]\s+([^\n]{15,220})$")


class Suggestion(BaseModel):
    section: str  # which team-knowledge section it would go into
    kind: str  # from_docs | promote | still_raised | missing (something to write; nothing to add as is)
    text: str  # the line to add (or, for still_raised, the rule concerned)
    why: str
    evidence: list[str] = []


def glossary_from_docs(chunks: list[dict[str, Any]], known: list[str], limit: int = 20) -> list[Suggestion]:
    """"Term: definition" lines in the team's indexed pages and docs that the glossary does not have yet. chunks: {text, title, url}."""
    have = {k.lower() for k in known}
    out: dict[str, Suggestion] = {}
    for c in chunks:
        for line in (c.get("text") or "").splitlines():
            m = _DEF.match(line)
            if not m or "http" in m.group(2):
                continue
            term = m.group(1).strip()
            if term.lower() in have or term.lower() in out:
                continue
            out[term.lower()] = Suggestion(section="glossary", kind="from_docs", text=f"{term}: {m.group(2).strip()}", why=f"defined in {c.get('title') or 'a document'}",
                                           evidence=[c["url"]] if c.get("url") else [])
            if len(out) >= limit:
                return list(out.values())
    return list(out.values())


def systems_from_readmes(repos: list[str], readmes: dict[str, str], known: list[str]) -> list[Suggestion]:
    """One line per team repository the systems section does not mention yet, from the first sentence of its README."""
    have = " ".join(known).lower()
    out = []
    for repo in repos:
        name = repo.split("/")[-1]
        if name.lower() in have:
            continue
        first = next((l.strip() for l in (readmes.get(repo) or "").splitlines() if l.strip() and not l.lstrip().startswith(("#", "!", "[", "<", "`"))), "")
        if first:
            sentence = re.split(r"(?<=[.!?])\s", first)[0][:200]
            out.append(Suggestion(section="systems", kind="from_docs", text=f"{name}: {sentence}",
                                  why=f"from the README of {repo}, one of the team's repositories"))
        else:  # nothing to go on: ask for a line instead of offering filler to add
            out.append(Suggestion(section="systems", kind="missing", text=f"{name}: what it does, who owns it, what it calls",
                                  why=f"{repo} is one of the team's repositories and has no line here yet"))
    return out


def promotions(lessons: list[dict[str, Any]], team: str, team_repos: list[str], rules: list[str], similarity: float = 0.55) -> list[Suggestion]:
    """Learned rules that belong to the whole team: lessons scoped to the team, and the same lesson adopted in two or more of its
    repositories. A rule for one repository stays in that repository's CLAUDE.md."""
    live = [l for l in lessons if l["status"] == "approved"]
    out, taken = [], [_words(r) for r in rules]
    groups: list[dict[str, Any]] = []
    for l in live:
        if l["scope"] == "team" and l.get("scope_value") == team:
            groups.append({"text": l["text"], "repos": {"team"}, "ids": [l["id"]]})
        elif l.get("repo") in team_repos:
            w = _words(l["text"])
            for g in groups:
                if _jaccard(w, _words(g["text"])) >= similarity:
                    g["repos"].add(l["repo"]); g["ids"].append(l["id"])
                    break
            else:
                groups.append({"text": l["text"], "repos": {l["repo"]}, "ids": [l["id"]]})
    for g in groups:
        if ("team" in g["repos"] or len(g["repos"]) >= 2) and all(_jaccard(_words(g["text"]), t) < similarity for t in taken):
            where = "taught for the whole team" if "team" in g["repos"] else f"learned in {len(g['repos'])} of the team's repositories"
            out.append(Suggestion(section="rules", kind="promote", text=g["text"], why=where, evidence=[f"L{i}" for i in g["ids"]]))
    return out


def rules_still_raised(rules: list[str], feedback: list[Feedback], team: str, similarity: float = 0.4, min_count: int = 2) -> list[Suggestion]:
    """Written rules that reviewers keep commenting on in the team's PRs: the agent is not following the rule, or the rule is no longer
    what the team does. Either way a person should look at it."""
    out = []
    for r in rules:
        rw = _words(r)
        hits = {f.task_id: f.body for f in feedback if f.kind == "review" and f.team == team and _jaccard(rw, _words(f.body)) >= similarity}
        if len(hits) >= min_count:
            out.append(Suggestion(section="rules", kind="still_raised", text=r, why=f"reviewers raised this on {len(hits)} PRs, e.g. “{next(iter(hits.values()))[:200]}”",
                                  evidence=sorted(hits)[:5]))
    return out
