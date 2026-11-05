"""Lessons: team rules added with "@sif remember", approved by role, and delivered to the agent as a trusted block.

Pure functions only (no I/O). Lessons are the one place where text is allowed to act as an instruction for the agent,
so everything that enters passes these gates first: allowed author, size and shape, injection and secret scans (in
portal-api), and approval. The agent can suggest a lesson but never approve one.
"""

import fnmatch
import hashlib
import re
from datetime import datetime
from enum import StrEnum
from math import ceil

from pydantic import BaseModel

from sif_contracts.settings import Settings


class Scope(StrEnum):
    PATH = "path"  # a glob inside one repo
    REPO = "repo"
    TEAM = "team"
    GLOBAL = "global"


class Role(StrEnum):
    NONE = "none"  # not on any allowlist: cannot add lessons
    MEMBER = "member"
    MAINTAINER = "maintainer"
    TEAM_LEAD = "team_lead"
    ADMIN = "admin"


class Status(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISABLED = "disabled"


class Source(StrEnum):
    HUMAN_COMMENT = "human_comment"
    AGENT_SUGGESTION = "agent_suggestion"
    PR_REVIEW = "pr_review"
    MINED = "mined"


# Lessons no person wrote: mined from reviews, CI or outcomes, or suggested by the agent. Even once approved, they are held back from a
# share of tasks (held_out) so the factory can measure whether they help, and retire them automatically when they do not.
MACHINE = frozenset({Source.PR_REVIEW, Source.MINED, Source.AGENT_SUGGESTION})

SPECIFICITY = {Scope.PATH: 4, Scope.REPO: 3, Scope.TEAM: 2, Scope.GLOBAL: 1}

# Who may add which scope without review. Everyone allowlisted may still *propose* any scope.
_AUTO = {
    Scope.PATH: {Role.MAINTAINER, Role.TEAM_LEAD, Role.ADMIN},
    Scope.REPO: {Role.MAINTAINER, Role.TEAM_LEAD, Role.ADMIN},
    Scope.TEAM: {Role.TEAM_LEAD, Role.ADMIN},
    Scope.GLOBAL: {Role.ADMIN},
}


class Command(BaseModel):
    action: str  # "remember" or "forget"
    text: str = ""
    scope: Scope = Scope.REPO
    scope_value: str | None = None  # glob for path scope; repo/team default to the caller's context
    ttl_days: int | None = None
    lesson_id: int | None = None


def parse_command(comment: str, s: Settings) -> Command | None:
    """Parses '@sif remember <rule> [scope=repo|team|global|path:<glob>] [ttl=90d]' and '@sif forget L12'.

    '@sif-remember' works too. Check this before treating a comment as an answer to a question.
    """
    mention = re.escape(s.mention)
    cmds = f"{re.escape(s.cmd_remember)}|{re.escape(s.cmd_forget)}"
    m = re.search(rf"(?<![\w@]){mention}[\s:,\-]*({cmds})\b[\s:,\-]*(.*)", comment, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    action = "remember" if m.group(1).lower() == s.cmd_remember.lower() else "forget"
    rest = m.group(2).strip()

    if action == "forget":
        idm = re.search(r"\bL?(\d+)\b", rest, re.IGNORECASE)
        return Command(action="forget", lesson_id=int(idm.group(1))) if idm else None

    scope, value, ttl = Scope.REPO, None, None
    sm = re.search(r"\bscope\s*=\s*(path:\S+|repo|team|global)", rest, re.IGNORECASE)
    if sm:
        raw = sm.group(1)
        if raw.lower().startswith("path:"):
            scope, value = Scope.PATH, raw[5:]
        else:
            scope = Scope(raw.lower())
        rest = rest.replace(sm.group(0), " ")
    tm = re.search(r"\bttl\s*=\s*(\d+)\s*d\b", rest, re.IGNORECASE)
    if tm:
        ttl = int(tm.group(1))
        rest = rest.replace(tm.group(0), " ")
    return Command(action="remember", text=clean_text(rest), scope=scope, scope_value=value, ttl_days=ttl)


def clean_text(text: str) -> str:
    """One line, single spaces, no markup that could pose as structure inside the trusted rules block."""
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"[`<>#|]", "", text)
    return re.sub(r" {2,}", " ", text).strip(" -:")


def text_error(text: str, s: Settings) -> str | None:
    if len(text) < 10:
        return "A lesson needs at least 10 characters: say what to do and when."
    if len(text) > s.lesson_max_chars:
        return f"A lesson can be at most {s.lesson_max_chars} characters; split it into separate rules."
    return None


class Decision(StrEnum):
    APPROVED = "approved"
    CANDIDATE = "candidate"  # waits for review on the Learning page
    DENIED = "denied"


def decide(scope: Scope, role: Role, source: Source, s: Settings, *, flagged: bool = False) -> Decision:
    """Role-based approval. Only a human comment from a sufficiently senior author, with a clean scan, is auto-approved."""
    if role is Role.NONE:
        return Decision.DENIED
    if flagged or source is not Source.HUMAN_COMMENT or not s.lesson_auto_approve:
        return Decision.CANDIDATE
    return Decision.APPROVED if role in _AUTO[scope] else Decision.CANDIDATE


def can_forget(requester: str, role: Role, author: str) -> bool:
    """Authors can remove their own lessons; maintainers and above can remove any."""
    return role in {Role.MAINTAINER, Role.TEAM_LEAD, Role.ADMIN} or (role is not Role.NONE and requester == author)


class Lesson(BaseModel):
    id: int
    text: str
    scope: Scope
    scope_value: str | None = None
    repo: str | None = None
    status: Status = Status.APPROVED
    created_at: datetime
    expires_at: datetime | None = None
    use_count: int = 0
    source: Source = Source.HUMAN_COMMENT


class TaskContext(BaseModel):
    repo: str | None = None
    team: str | None = None
    paths: list[str] = []
    query: str = ""


def matches(lesson: Lesson, ctx: TaskContext) -> bool:
    match lesson.scope:
        case Scope.GLOBAL:
            return True
        case Scope.TEAM:
            return bool(ctx.team) and lesson.scope_value == ctx.team
        case Scope.REPO:
            return bool(ctx.repo) and lesson.scope_value == ctx.repo
        case Scope.PATH:
            return bool(ctx.repo) and lesson.repo == ctx.repo and bool(lesson.scope_value) and any(
                fnmatch.fnmatch(p, lesson.scope_value) for p in ctx.paths)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]{3,}", text.lower())}


def tokens(text: str) -> int:
    return ceil(len(text) / 4) + 8  # rough: ~4 characters per token plus the bullet and id


def select_lessons(lessons: list[Lesson], ctx: TaskContext, now: datetime, s: Settings) -> list[Lesson]:
    """Approved, unexpired, in-scope lessons: most specific first, then most related to the task, within the budget."""
    q = _words(ctx.query)
    live = [l for l in lessons if l.status is Status.APPROVED and (l.expires_at is None or l.expires_at > now) and matches(l, ctx)]
    live.sort(key=lambda l: (-SPECIFICITY[l.scope], -len(q & _words(l.text)), -l.use_count, -l.created_at.timestamp()))
    chosen, used = [], 0
    for l in live:
        if len(chosen) >= s.lesson_max_in_prompt or used + tokens(l.text) > s.lesson_token_budget:
            continue  # skip an oversized one but keep looking for smaller ones
        chosen.append(l)
        used += tokens(l.text)
    return chosen


def held_out(task_id: str, lesson: Lesson, pct: int) -> bool:
    """True if this task is in the comparison group for this machine-proposed lesson. Deterministic, so every context pack built for
    the same task (first attempt and each review round) agrees, and independent per lesson, so the groups are random with respect to
    each other. Lessons a person taught are always delivered."""
    if lesson.source not in MACHINE or pct <= 0:
        return False
    return int(hashlib.sha256(f"{task_id}:{lesson.id}".encode()).hexdigest()[:8], 16) % 100 < pct


def render_rules(lessons: list[Lesson]) -> str:
    """The trusted block placed in CONTEXT.md / the system prompt, separate from untrusted <untrusted> blocks."""
    if not lessons:
        return ""
    lines = [f"- [L{l.id}] {l.text}" for l in lessons]
    return ("## Team rules (approved by your team)\n"
            "Follow these unless a human tells you otherwise in this ticket. Cite the id when a rule shapes your work.\n"
            + "\n".join(lines) + "\n")
