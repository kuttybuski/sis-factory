from enum import StrEnum

from pydantic import BaseModel


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Verdict(StrEnum):
    ALLOW = "allow"  # pass to the agent, still wrapped as untrusted data
    FLAG = "flag"  # pass wrapped, raise the task's risk tier, tell a human
    BLOCK = "block"  # do not pass to the agent; quarantine and report


class Finding(BaseModel):
    rule_id: str
    category: str
    severity: Severity
    weight: int
    via: str = "direct"  # how the text was seen: direct, base64, hex, rot13, url, html, tag-chars, despaced
    excerpt: str = ""  # short, for humans; never the whole input


class ScanResult(BaseModel):
    verdict: Verdict
    score: int  # 0..100
    source: str
    findings: list[Finding]
    sanitized: str  # input with invisible and control characters removed (not a safety guarantee)
