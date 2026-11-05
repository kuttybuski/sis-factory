"""Scoring a replay (pure): how close an agent's patch on a past ticket comes to the change people actually merged.

The reference is the diff from the ticket's base commit to the merged head, so it includes review revisions and human fixups: what the
team finally accepted. A replay that lands there without the review rounds is what a better lesson should produce.

Similarity is a proxy, not a verdict on correctness: two different correct fixes score lower than they deserve. It is fair for comparing
two arms on the same ticket (with and without a lesson), which is what the replay benchmark does, because the proxy's bias is the same
on both sides."""

import re

_HEADER = re.compile(r"^(diff --git|index |--- |\+\+\+ |@@|new file mode|deleted file mode|similarity index|rename (from|to) )")
_FILE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.MULTILINE)


def files(patch: str) -> set[str]:
    return {b for _, b in _FILE.findall(patch or "")}


def changed_lines(patch: str) -> set[str]:
    """Added and removed lines with their sign, whitespace collapsed, so formatting alone does not count as a difference."""
    out = set()
    for line in (patch or "").splitlines():
        if _HEADER.match(line) or line[:1] not in ("+", "-"):
            continue
        body = " ".join(line[1:].split())
        if body:
            out.add(line[0] + body)
    return out


def score(reference: str, patch: str) -> float:
    """0..1: 40% which files were touched (Jaccard), 60% which lines changed (F1 against the reference)."""
    ref_files, got_files = files(reference), files(patch)
    if not got_files or not ref_files:
        return 0.0
    file_part = len(ref_files & got_files) / len(ref_files | got_files)
    ref_lines, got_lines = changed_lines(reference), changed_lines(patch)
    both = len(ref_lines & got_lines)
    line_part = 0.0 if not both else 2 * both / (len(ref_lines) + len(got_lines))
    return round(0.4 * file_part + 0.6 * line_part, 3)
