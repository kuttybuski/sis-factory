"""The daily knowledge PR (pure): what the factory learned about a repository in the last day, proposed as one pull request that adds the
new lessons to a managed block in the repository's CLAUDE.md.

The PR is the review. Merging it adopts every lesson still in the block; deleting a line before merging rejects that lesson; closing the PR
rejects them all. Lines already in the block are never rewritten, so edits people make there are kept, and a lesson whose line someone
removes later is disabled at the next merge. Once adopted, a lesson lives in the repository: Claude Code reads CLAUDE.md, and the factory
stops sending it in the rules block."""

import difflib
import re
from datetime import date
from typing import Any

_START, _END = "<!-- sif:lessons:start -->", "<!-- sif:lessons:end -->"  # the managed block in AGENTS.md
HEADING = "## Lessons learned (proposed by sif in a daily PR; delete a line to drop that rule)"
_TAG = re.compile(r"\(sif L(\d+)\)")
_PLACEHOLDER = "_No approved lessons yet._"


def line(lesson: dict[str, Any]) -> str:
    return f"- {lesson['text']} _(sif L{lesson['id']})_"


def append_lessons(existing: str, lessons: list[dict[str, Any]]) -> str:
    """The file with the lessons added at the end of the managed block (created if missing). Lessons already in the block are skipped."""
    present = block_ids(existing)
    new = [line(l) for l in lessons if l["id"] not in present]
    if _START in existing and _END in existing:
        a, b = existing.index(_START), existing.index(_END)
        inside = [x for x in existing[a + len(_START):b].strip("\n").split("\n") if x.strip() and x.strip() != _PLACEHOLDER]
        block = "\n".join([_START, *inside, *new, _END])
        return existing[:a] + block + existing[b + len(_END):]
    block = "\n".join([_START, HEADING, *new, _END])
    return existing.rstrip("\n") + ("\n\n" if existing.strip() else "") + block + "\n"


def block_ids(md: str | None) -> set[int]:
    """Lesson ids tagged inside the managed block: what the repository has adopted."""
    if not md or _START not in md or _END not in md:
        return set()
    return {int(x) for x in _TAG.findall(md[md.index(_START):md.index(_END)])}


def file_patch(path: str, old: str | None, new: str) -> str:
    """A unified diff git apply accepts: creates the file when old is None, and marks a missing final newline."""
    def lines(text: str) -> list[str]:
        out = text.splitlines(keepends=True)
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n\\ No newline at end of file\n"
        return out

    body = "".join(difflib.unified_diff(lines(old or ""), lines(new), fromfile="/dev/null" if old is None else f"a/{path}", tofile=f"b/{path}"))
    head = f"diff --git a/{path} b/{path}\n" + ("new file mode 100644\n" if old is None else "")
    return head + body


def title(repo: str, n: int, day: date) -> str:
    return f"sif: {n} lesson{'s' if n != 1 else ''} learned in {repo} ({day.isoformat()})"


def pr_body(lessons: list[dict[str, Any]], path: str) -> str:
    """Why each lesson is proposed, with its evidence and any replay result, and how to decide."""
    rows = []
    for l in lessons:
        why = {"mined": "recurring review feedback, CI failures or fixes people made on the factory's PRs", "pr_review": "recurring feedback in this repository's PR reviews",
               "agent_suggestion": "suggested by the agent", "human_comment": f"asked for by {l.get('author', 'a person')}"}.get(l.get("source", ""), l.get("source", ""))
        ev = f" Replay: solved {l['eval_candidate']}/{l['eval_n']} past tickets with it, {l['eval_baseline']}/{l['eval_n']} without." if l.get("eval_n") else ""
        src = f" ([evidence]({l['source_url']}))" if l.get("source_url") else ""
        rows.append(f"- **L{l['id']}** {l['text']}\n  _Why:_ {why}.{ev}{src}")
    return (f"What the factory learned since its last knowledge PR, added to `{path}` so every agent working here follows it.\n\n" + "\n".join(rows) +
            "\n\n**How to decide:** merge to adopt these rules. Delete a line before merging to reject that rule, or close the PR to reject all of them. "
            "Rules you adopt are kept here, under review like code; the factory stops sending them separately.\n\n---\nOpened by sif. A person must review and merge it.")
