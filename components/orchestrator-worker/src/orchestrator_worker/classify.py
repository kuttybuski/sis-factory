"""Triage rules. Deterministic and free; a cheap model can refine them later, but results are never needed to be reproducible by an LLM."""

import re

HIGH = re.compile(r"\b(auth\w*|password|secret|credential\w*|payments?|billing|migrat\w+|drop table|delete|production|prod|security|encrypt\w*|permission\w*|pii|gdpr)\b", re.IGNORECASE)
MEDIUM = re.compile(r"\b(api|schema|database|db|config\w*|dependenc\w+|upgrade|refactor\w*|endpoint|queue|cache)\b", re.IGNORECASE)
DONE = re.compile(r"(acceptance criteria|expected|should|must|done when|definition of done|so that|steps to reproduce)", re.IGNORECASE)
# A direct instruction ("change X to Y", "rename A to B", "fix the typo in ...") already says what done is: the change, made.
ACTION = re.compile(r"^\W*(please\s+)?(change|replace|rename|fix|correct|add|remove|delete|update|bump|upgrade|downgrade|move|set|make|use|revert|swap|translate)\b",
                    re.IGNORECASE)


def instruction(title: str, description: str) -> bool:
    """A line of the title or description that starts with an action verb and names what to act on (four words or more)."""
    return any(ACTION.match(line) and len(line.split()) >= 4 for line in f"{title}\n{description}".splitlines())


def ticket_quality(title: str, description: str, kind: str = "task") -> tuple[bool, list[str]]:
    """A ticket an agent can act on says what is wrong and what 'done' looks like. Epics and features state goals: their stories carry the acceptance criteria."""
    if kind not in ("epic", "feature") and instruction(title, description):
        return True, []
    missing = []
    text = description.strip()
    if len(text) < 40:
        missing.append("a description of at least a couple of sentences")
    if kind not in ("epic", "feature") and not DONE.search(text):
        missing.append("what 'done' looks like (acceptance criteria or the expected behaviour)")
    return not missing, missing


def risk_tier(title: str, description: str) -> str:
    text = f"{title}\n{description}"
    return "high" if HIGH.search(text) else "medium" if MEDIUM.search(text) else "low"


def slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-") or "change"
