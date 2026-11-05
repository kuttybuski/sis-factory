"""Scores untrusted text for prompt injection. Heuristic: it catches known patterns and common obfuscation, not everything."""

import hashlib
import os
import re

from injection_guard.classifier import Classifier
from injection_guard.models import Finding, ScanResult, Severity, Verdict
from injection_guard.normalize import normalize, variants
from injection_guard.rules import RULES

MAX_CHARS = 200_000
EXCERPT = 80

# (flag, block) thresholds by trust profile. Override with INJECTION_<PROFILE>_FLAG / INJECTION_<PROFILE>_BLOCK.
_DEFAULTS = {"untrusted": (25, 60), "semi_trusted": (35, 70)}

SOURCE_PROFILE = {
    "jira_ticket": "untrusted", "jira_comment": "untrusted", "pr_description": "untrusted", "pr_review": "untrusted",
    "pr_comment": "untrusted", "commit_message": "untrusted", "tool_output": "untrusted", "web": "untrusted",
    "confluence": "semi_trusted", "repo_file": "semi_trusted",
}


def thresholds_for(source: str, env: dict[str, str] | None = None) -> tuple[int, int]:
    env = os.environ if env is None else env
    profile = SOURCE_PROFILE.get(source, "untrusted")  # unknown sources get the strictest profile
    flag, block = _DEFAULTS[profile]
    key = profile.upper()
    return int(env.get(f"INJECTION_{key}_FLAG", flag)), int(env.get(f"INJECTION_{key}_BLOCK", block))


def combine(weights: list[int]) -> int:
    """Strongest signal counts fully; each further one counts for half as much as the previous. Capped at 100."""
    total, scale = 0.0, 1.0
    for w in sorted(weights, reverse=True):
        total += w * scale
        scale /= 2
    return min(100, round(total))


def scan(text: str, source: str = "unknown", *, classifier: Classifier | None = None, env: dict[str, str] | None = None) -> ScanResult:
    if len(text) > MAX_CHARS:
        raise ValueError(f"text longer than {MAX_CHARS} characters; split it before scanning")

    n = normalize(text)
    found: dict[str, Finding] = {}  # one finding per rule: the strongest

    def add(rule_id: str, category: str, severity: Severity, weight: int, via: str, excerpt: str = "") -> None:
        old = found.get(rule_id)
        if old is None or weight > old.weight:
            found[rule_id] = Finding(rule_id=rule_id, category=category, severity=severity, weight=weight, via=via, excerpt=excerpt[:EXCERPT])

    for via, view in variants(n):
        for rule in RULES:
            if via == "rot13" and rule.severity not in (Severity.HIGH, Severity.CRITICAL):
                continue  # rot13 of ordinary English rarely matches; only trust strong hits there
            m = rule.pattern.search(view)
            if m:
                bonus = 10 if via != "direct" else 0  # hiding the payload is itself suspicious
                add(rule.id, rule.category, rule.severity, rule.weight + bonus, via, m.group())

    if n.smuggled:
        add("ascii-smuggling", "obfuscation", Severity.CRITICAL, 60, "tag-chars", n.smuggled)
    elif n.invisible_count:
        add("invisible-characters", "obfuscation", Severity.MEDIUM, 20 if n.invisible_count < 5 else 30, "direct", f"{n.invisible_count} hidden characters")
    if n.mixed_script_words:
        add("homoglyphs", "obfuscation", Severity.MEDIUM, 20, "direct", ", ".join(n.mixed_script_words))

    if classifier is not None:
        p = classifier.score(n.text)
        if p >= 0.5:
            add("model-classifier", "model", Severity.HIGH if p >= 0.8 else Severity.MEDIUM, round(p * 60), "direct", f"p={p:.2f}")

    findings = sorted(found.values(), key=lambda f: f.weight, reverse=True)
    score = combine([f.weight for f in findings])
    flag, block = thresholds_for(source, env)
    verdict = Verdict.BLOCK if score >= block else Verdict.FLAG if score >= flag else Verdict.ALLOW
    return ScanResult(verdict=verdict, score=score, source=source, findings=findings, sanitized=n.text)


def fingerprint(text: str) -> str:
    """Identifies an attempt without keeping its text: a hash of the normalized text with case and spacing folded, so the same attack
    pasted again (or re-spaced, or with hidden characters added) has the same fingerprint. A team can block a fingerprint outright."""
    folded = re.sub(r"\s+", " ", normalize(text[:MAX_CHARS]).text).strip().casefold()
    return hashlib.sha256(folded.encode()).hexdigest()
