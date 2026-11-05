"""Jira Cloud's v3 API speaks Atlassian Document Format. Minimal conversions in both directions."""

import re


def adf_to_text(node) -> str:
    """Plain text from an ADF document. Real mentions become [~accountid:ID], the form sis_contracts.policy understands."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    t = node.get("type")
    if t == "text":
        return node.get("text", "")
    if t == "mention":
        return f"[~accountid:{node['attrs']['id']}]" if node.get("attrs", {}).get("id") else node.get("attrs", {}).get("text", "")
    if t == "hardBreak":
        return "\n"
    if t == "inlineCard":
        return node.get("attrs", {}).get("url", "")
    inner = "".join(adf_to_text(c) for c in node.get("content", []))
    if t in ("paragraph", "heading"):
        return inner + "\n\n"
    if t in ("listItem", "tableRow"):
        return inner + "\n"
    if t == "codeBlock":
        return f"```\n{inner}\n```\n"
    return inner


def clean(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def text_to_adf(text: str) -> dict:
    """Paragraphs on blank lines, hard breaks on single newlines. No markup: what we write is plain."""
    paragraphs = []
    for block in re.split(r"\n\s*\n", text.strip()):
        content = []
        for i, line in enumerate(block.split("\n")):
            if i:
                content.append({"type": "hardBreak"})
            if line:
                content.append({"type": "text", "text": line})
        if content:
            paragraphs.append({"type": "paragraph", "content": content})
    return {"type": "doc", "version": 1, "content": paragraphs or [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]}
