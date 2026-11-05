"""Checks what leaves the system (PR text, commit messages, Jira comments, tool arguments) for leaked secrets and exfiltration."""

import re
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel

from injection_guard.canary import leaked
from injection_guard.models import Finding, Severity

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple((n, re.compile(p)) for n, p in (
    ("github-token", r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ("aws-access-key", r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("private-key", r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("openai-style-key", r"\bsk-[A-Za-z0-9]{32,}\b"),
    ("slack-token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("atlassian-token", r"\bATATT[A-Za-z0-9_\-=]{20,}"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    ("bearer-token", r"\bBearer\s+[A-Za-z0-9._~+/-]{24,}=*"),
))

_URL = re.compile(r"https?://[^\s)<>\"']+", re.IGNORECASE)
_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(https?://[^)\s]+)", re.IGNORECASE)
MIN_LITERAL = 8


class OutboundResult(BaseModel):
    ok: bool
    findings: list[Finding]


def _f(rule_id: str, category: str, sev: Severity, weight: int, excerpt: str = "") -> Finding:
    return Finding(rule_id=rule_id, category=category, severity=sev, weight=weight, excerpt=excerpt[:40])


def check_outbound(text: str, *, allowed_hosts: frozenset[str] | set[str] = frozenset(), canaries: list[str] | tuple[str, ...] = (),
                   literals: list[str] | tuple[str, ...] = ()) -> OutboundResult:
    """ok is False if anything high or critical is found.

    literals are exact secret values the caller holds (for example the GitHub token). Pass them in-process: do not
    send real secrets to the HTTP service. Excerpts never include the matched secret.
    """
    findings: list[Finding] = []

    for c in leaked(text, canaries):
        findings.append(_f("canary-leak", "prompt-leak", Severity.CRITICAL, 100, "canary token in output"))
    compact = "".join(text.split())
    for lit in literals:
        if len(lit) >= MIN_LITERAL and (lit in text or lit in compact):
            findings.append(_f("secret-literal", "secret-leak", Severity.CRITICAL, 100, "known secret value in output"))
    for name, pat in SECRET_PATTERNS:
        if pat.search(text):
            findings.append(_f(name, "secret-leak", Severity.CRITICAL, 90, "secret-shaped value"))

    images = {m.group(1) for m in _IMAGE.finditer(text)}
    for url in {m.group().rstrip(".,;") for m in _URL.finditer(text)}:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host in allowed_hosts:
            continue
        params = parse_qsl(parts.query, keep_blank_values=True)
        long_value = any(len(v) >= 20 for _, v in params)
        if url in images:
            findings.append(_f("image-beacon", "exfiltration", Severity.HIGH, 70, host))
        elif long_value:
            findings.append(_f("url-with-payload", "exfiltration", Severity.HIGH, 60, host))
        else:
            findings.append(_f("external-url", "exfiltration", Severity.LOW, 10, host))

    ok = not any(f.severity in (Severity.HIGH, Severity.CRITICAL) for f in findings)
    return OutboundResult(ok=ok, findings=findings)


def redact(text: str) -> str:
    """Masks secret-shaped values. Use before showing text to a human or storing it."""
    for name, pat in SECRET_PATTERNS:
        text = pat.sub(f"[redacted:{name}]", text)
    return text
