"""Everything that gets indexed passes through here: secrets and personal data are redacted and injection attempts are quarantined."""

from dataclasses import dataclass

from injection_guard import Verdict, redact, scan

from sis_context.models import Chunk
from sis_context.pii import enabled, redact_pii

# How much to trust the text, by the kind of document it came from (see injection_guard.scanner.SOURCE_PROFILE).
# Wiki pages can be edited by many people with little review, so they get the strict profile; repo files are code-reviewed.
SOURCE_FOR = {"page": "web", "note": "web",  # "note" = pushed in from an arbitrary system
               "doc": "repo_file", "diff": "repo_file", "summary": "pr_description",
              "thread": "pr_review", "qa": "jira_comment"}


@dataclass
class Screened:
    kept: list[Chunk]
    quarantined: list[tuple[Chunk, list[str]]]  # (chunk, rule ids)
    redactions: int


def screen(chunks: list[Chunk], doc_kind: str, pii: tuple[str, ...] | None = None) -> Screened:
    kept, quarantined, redactions = [], [], 0
    pii = enabled() if pii is None else pii
    for c in chunks:
        source = SOURCE_FOR.get(c.kind) or SOURCE_FOR.get(doc_kind, "web")
        text, _ = redact_pii(redact(c.text), pii)
        redactions += text != c.text
        result = scan(text[:200_000], source)
        rules = [f.rule_id for f in result.findings]
        if result.verdict is Verdict.BLOCK:
            quarantined.append((c.model_copy(update={"text": text}), rules))  # the quarantine excerpt is shown on the dashboard: redacted too
        else:
            kept.append(c.model_copy(update={"text": text, "flagged": result.verdict is Verdict.FLAG}))
    return Screened(kept=kept, quarantined=quarantined, redactions=redactions)
