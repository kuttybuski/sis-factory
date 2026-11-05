"""Team knowledge (pure): what a team's lead or anyone who knows writes down about how the team works. It covers the basics self-learning
cannot find: the vocabulary, the systems and who owns them, the rules that must never be broken, what "done" means, how work flows.

Self-learning finds the subtle conventions from corrections; this page is for what is known up front and too important to learn by getting
it wrong. Like lessons, it reaches the agent as trusted instructions, so it is role-gated, screened and versioned (in portal-api). Only what
is relevant to a ticket is sent: every rule and the definition of done always, glossary terms and systems the ticket mentions, and the
process notes while they fit the budget."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    help: str
    max_chars: int
    entries: bool = False  # one "Name: what it is" per line, matched against the ticket
    always: bool = False  # sent with every ticket


SECTIONS = (
    Section("rules", "Non-negotiables", "Rules that must never be broken: compliance, security, data handling, SLAs, “never touch X without Y”. "
            "One per line. Every one is sent with every ticket, so keep them few and concrete.", 3000, always=True),
    Section("done", "Definition of done", "What a finished change includes here: tests, feature flags, migrations, docs, changelog.", 2000, always=True),
    Section("glossary", "Glossary", "Domain terms as this team uses them, one per line: “Settlement: the nightly transfer of captured payments to merchants”. "
            "A term is sent when a ticket mentions it.", 8000, entries=True),
    Section("systems", "Systems and ownership", "What the team owns, what it calls and who owns the neighbours, one per line: "
            "“billing-api: invoices and payment runs; owned by us; calls ledger (Finance team)”. A system is sent when a ticket mentions it.", 8000, entries=True),
    Section("process", "How we work", "Branching, reviews, releases, environments, who decides what.", 3000),
    Section("notes", "Other things a new senior would need to know", "Anything else: history, traps, things that look wrong but are on purpose.", 3000),
)
BY_KEY = {s.key: s for s in SECTIONS}
STALE_AFTER = timedelta(days=90)
_ENTRY = re.compile(r"^\s*[-*•]?\s*\**([^:\n*]{2,60}?)\**\s*[:—–]\s+(.+)$")


def text_error(key: str, content: str) -> str | None:
    s = BY_KEY.get(key)
    if s is None:
        return f"unknown section {key!r}"
    if len(content) > s.max_chars:
        return f"{s.title} can be at most {s.max_chars} characters; keep it to what an agent needs, and link to the rest."
    return None


def entries(text: str) -> list[tuple[str, str]]:
    """(name, description) for each "Name: description" line."""
    return [(m.group(1).strip(), m.group(2).strip()) for line in (text or "").splitlines() if (m := _ENTRY.match(line))]


def lines(text: str) -> list[str]:
    """The non-empty lines of a section, without bullets: the rules, one by one."""
    return [l.strip().lstrip("-*• ").strip() for l in (text or "").splitlines() if l.strip().lstrip("-*• ").strip()]


def mentioned(name: str, query: str) -> bool:
    """Whole-word, case-insensitive; "billing-api" also matches "billing api"."""
    words = [re.escape(w) for w in re.split(r"[\s-]+", name.lower().strip()) if w]
    return bool(words) and re.search(r"(?<![\w-])" + r"[\s-]+".join(words) + r"(?![\w-])", query.lower()) is not None


def for_task(team: str, sections: dict[str, str], query: str, budget_chars: int = 6000) -> str:
    """The team-knowledge block for one ticket, most important first, within the budget. Empty if the team wrote nothing."""
    parts: list[str] = []
    used = 0

    def add(title: str, body: str) -> None:
        nonlocal used
        body = body.strip()
        if body and used + len(body) <= budget_chars:
            parts.append(f"### {title}\n{body}")
            used += len(body)

    for s in SECTIONS:
        text = sections.get(s.key, "")
        if s.always:
            add(s.title, text)
        elif s.entries:
            hits = [f"- {n}: {d}" for n, d in entries(text) if mentioned(n, query)]
            add(f"{s.title} (mentioned in this ticket)", "\n".join(hits))
        else:
            add(s.title, text)
    if not parts:
        return ""
    return (f"## Team knowledge ({team}, written by the team)\nTreat this as instructions from the team's lead. If the ticket conflicts with a "
            "non-negotiable, stop and ask.\n\n" + "\n\n".join(parts) + "\n")


def stale(confirmed_at: datetime | None, now: datetime) -> bool:
    return confirmed_at is None or now - confirmed_at > STALE_AFTER
