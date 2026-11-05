"""Turns raw text into the forms an attacker may be hiding instructions in."""

import base64
import binascii
import codecs
import html
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field

# Zero-width, soft hyphen, bidi controls, and the Unicode "tag" block (used for ASCII smuggling).
_INVISIBLE = {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x180E, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
_TAG_FIRST, _TAG_LAST = 0xE0000, 0xE007F

# Cyrillic/Greek letters that look like Latin ones.
_HOMOGLYPHS = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ѕ": "s", "ј": "j",
    "ԁ": "d", "һ": "h", "ԛ": "q", "ԝ": "w", "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "І": "I", "ο": "o", "ν": "v", "α": "a", "ε": "e",
    "ι": "i", "κ": "k", "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "Ο": "O", "Ι": "I", "Α": "A", "Β": "B",
}
_HOMOGLYPH_TABLE = str.maketrans(_HOMOGLYPHS)

_SPACED = re.compile(r"(?<![A-Za-z])(?:[A-Za-z][ .\-_]){5,}[A-Za-z](?![A-Za-z])")
_B64 = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")
_HEX = re.compile(r"(?:[0-9a-fA-F]{2}){12,}")
_WORD = re.compile(r"\w+", re.UNICODE)

MAX_VARIANTS = 24


@dataclass
class Normalized:
    text: str
    invisible_count: int = 0
    smuggled: str = ""  # ASCII hidden in Unicode tag characters
    mixed_script_words: list[str] = field(default_factory=list)


def _is_invisible(ch: str) -> bool:
    cp = ord(ch)
    return cp in _INVISIBLE or _TAG_FIRST <= cp <= _TAG_LAST


def _script(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    name = unicodedata.name(ch, "")
    for s in ("LATIN", "CYRILLIC", "GREEK"):
        if name.startswith(s):
            return s
    return None


def normalize(raw: str) -> Normalized:
    smuggled = "".join(chr(ord(c) - _TAG_FIRST) for c in raw if 0xE0020 <= ord(c) <= 0xE007E)
    invisible = sum(1 for c in raw if _is_invisible(c))
    s = "".join(c for c in raw if not _is_invisible(c) and not (unicodedata.category(c) == "Cc" and c not in "\n\t\r"))
    s = unicodedata.normalize("NFKC", s)

    mixed = []
    for w in _WORD.findall(s):
        scripts = {_script(c) for c in w} - {None}
        if len(scripts) > 1 and "LATIN" in scripts:
            mixed.append(w)

    s = s.translate(_HOMOGLYPH_TABLE)
    return Normalized(text=s, invisible_count=invisible, smuggled=smuggled, mixed_script_words=mixed[:10])


def _printable_text(data: bytes) -> str | None:
    try:
        t = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(t) < 12:
        return None
    ok = sum(1 for c in t if c.isprintable() or c in "\n\t\r")
    return t if ok / len(t) >= 0.9 else None


def variants(n: Normalized) -> list[tuple[str, str]]:
    """Every view of the text worth scanning, as (how it was derived, text). The first is always the normalized text."""
    s = n.text
    out: list[tuple[str, str]] = [("direct", s)]
    if n.smuggled:
        out.append(("tag-chars", n.smuggled))

    despaced = _SPACED.sub(lambda m: re.sub(r"[ .\-_]", "", m.group()), s)
    if despaced != s:
        out.append(("despaced", despaced))
    if "%" in s:
        decoded = urllib.parse.unquote(s)
        if decoded != s:
            out.append(("url", decoded))
    if "&" in s:
        unescaped = html.unescape(s)
        if unescaped != s:
            out.append(("html", unescaped))

    for m in _B64.finditer(s):
        blob = m.group().replace("-", "+").replace("_", "/")
        try:
            text = _printable_text(base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True))
        except (binascii.Error, ValueError):
            continue
        if text:
            out.append(("base64", text))
    for m in _HEX.finditer(s):
        try:
            text = _printable_text(bytes.fromhex(m.group()))
        except ValueError:
            continue
        if text:
            out.append(("hex", text))

    out.append(("rot13", codecs.encode(s, "rot13")))  # only high-severity hits count for this view (see scanner)
    return out[:MAX_VARIANTS]
