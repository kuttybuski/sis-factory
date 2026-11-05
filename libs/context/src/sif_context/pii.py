"""Personal data is taken out of documents before they are indexed: the index is searched for every task and its text is sent to the model
and can end up in PRs. Payment card numbers (Luhn-checked), US social security numbers, IBANs and email addresses are replaced by a
placeholder. SIF_REDACT_PII picks which (comma-separated; "none" turns it off)."""

import os
import re

KINDS = ("card", "ssn", "iban", "email")
_CARD = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")
_SSN = re.compile(r"(?<![\w-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\w-])")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")


def _luhn(digits: str) -> bool:
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def _iban_ok(s: str) -> bool:
    s = s.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def enabled(env: dict[str, str] | None = None) -> tuple[str, ...]:
    v = (env if env is not None else os.environ).get("SIF_REDACT_PII", ",".join(KINDS)).lower()
    return () if v in ("", "none", "off", "false") else tuple(k for k in KINDS if k in v.split(","))


def redact_pii(text: str, kinds: tuple[str, ...] = KINDS) -> tuple[str, int]:
    """The text with personal data replaced, and how many replacements were made."""
    count = 0

    def sub(pattern: re.Pattern, label: str, check=lambda m: True):
        nonlocal text, count

        def one(m: re.Match) -> str:
            nonlocal count
            if not check(m.group(0)):
                return m.group(0)
            count += 1
            return f"[{label} removed]"

        text = pattern.sub(one, text)

    if "card" in kinds:
        sub(_CARD, "card number", lambda s: 13 <= len(d := re.sub(r"\D", "", s)) <= 19 and _luhn(d))
    if "ssn" in kinds:
        sub(_SSN, "SSN")
    if "iban" in kinds:
        sub(_IBAN, "IBAN", _iban_ok)
    if "email" in kinds:
        sub(_EMAIL, "email")
    return text, count
