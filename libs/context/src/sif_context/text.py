"""Turns Confluence storage-format HTML and Markdown into chunks that keep their heading breadcrumb."""

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag

from sif_context.models import Chunk, approx_tokens

HEADINGS = {f"h{i}": i for i in range(1, 7)}
_BLOCKS = {*HEADINGS, "p", "li", "pre", "tr", "blockquote", "ac:plain-text-body"}
TARGET, MAX_TOKENS, MIN_TOKENS = 380, 600, 40


@dataclass
class Block:
    kind: str  # heading | text | code | row
    text: str
    level: int = 0


def html_to_blocks(html: str) -> list[Block]:
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[Block] = []

    def inline_text(tag: Tag) -> str:
        clone = BeautifulSoup(str(tag), "html.parser")
        for nested in clone.find_all(["ul", "ol"]):
            nested.decompose()
        return " ".join(clone.get_text(" ", strip=True).split())

    def walk(node: Tag, own_text: bool = True) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                t = " ".join(str(child).split())
                if t and own_text:  # a paragraph's own text was already emitted by its parent
                    out.append(Block("text", t))
            elif child.name in HEADINGS:
                if t := inline_text(child):
                    out.append(Block("heading", t, HEADINGS[child.name]))
            elif child.name in ("pre", "ac:plain-text-body"):
                if t := child.get_text().strip("\n"):
                    out.append(Block("code", t))
            elif child.name == "tr":
                cells = [" ".join(c.get_text(" ", strip=True).split()) for c in child.find_all(["th", "td"])]
                if any(cells):
                    out.append(Block("row", " | ".join(cells)))
            elif child.name in ("p", "li", "blockquote"):
                if t := inline_text(child):
                    out.append(Block("text", t))
                walk(child, own_text=False)  # nested lists and macros only
            elif child.name in ("script", "style"):
                continue
            else:
                walk(child)

    walk(soup)
    return out


def md_to_blocks(md: str) -> list[Block]:
    blocks: list[Block] = []
    para: list[str] = []
    in_code, code = False, []

    def flush() -> None:
        if para:
            blocks.append(Block("text", " ".join(para)))
            para.clear()

    for line in md.splitlines():
        if line.strip().startswith("```"):
            if in_code:
                blocks.append(Block("code", "\n".join(code)))
                code, in_code = [], False
            else:
                flush()
                in_code = True
            continue
        if in_code:
            code.append(line)
        elif m := re.match(r"^(#{1,6})\s+(.*)$", line):
            flush()
            blocks.append(Block("heading", m.group(2).strip(), len(m.group(1))))
        elif not line.strip():
            flush()
        else:
            para.append(line.strip())
    flush()
    if code:
        blocks.append(Block("code", "\n".join(code)))
    return blocks


def _split_long(text: str, limit: int) -> list[str]:
    if approx_tokens(text) <= limit:
        return [text]
    parts, cur = [], ""
    for piece in re.split(r"(?<=[.!?\n])\s+", text):
        while len(piece) > limit * 4:  # a single enormous line
            parts.append(piece[: limit * 4])
            piece = piece[limit * 4:]
        if cur and approx_tokens(cur + " " + piece) > limit:
            parts.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        parts.append(cur)
    return parts


def chunk_blocks(blocks: list[Block], title: str, breadcrumb: str = "") -> list[Chunk]:
    """Splits at headings and around ~380 tokens. Code blocks stay whole (unless huge). Tiny sections merge into the next."""
    prefix = " > ".join(x for x in (breadcrumb, title) if x)
    stack: list[tuple[int, str]] = []
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_path = prefix

    def path() -> str:
        return " > ".join([prefix, *(t for _, t in stack)]) if prefix else " > ".join(t for _, t in stack)

    def flush(force: bool = False) -> None:
        nonlocal buf, buf_path
        text = "\n".join(buf).strip()
        if text and (force or approx_tokens(text) >= MIN_TOKENS):
            chunks.append(Chunk(text=text, heading_path=buf_path, kind="code" if text.startswith("```") else "text"))
            buf = []
        buf_path = path()

    for b in blocks:
        if b.kind == "heading":
            flush()
            while stack and stack[-1][0] >= b.level:
                stack.pop()
            stack.append((b.level, b.text))
            if not buf:
                buf_path = path()
            continue
        pieces = _split_long(b.text, MAX_TOKENS)
        for piece in pieces:
            text = f"```\n{piece}\n```" if b.kind == "code" else piece
            if buf and approx_tokens("\n".join(buf) + text) > TARGET:
                flush(force=True)
            buf.append(text)
            buf_path = path()  # a chunk is labelled by its latest (most specific) heading
    flush(force=True)
    return chunks


def html_to_chunks(html: str, title: str, breadcrumb: str = "") -> list[Chunk]:
    return chunk_blocks(html_to_blocks(html), title, breadcrumb)


def md_to_chunks(md: str, title: str, breadcrumb: str = "") -> list[Chunk]:
    return chunk_blocks(md_to_blocks(md), title, breadcrumb)
