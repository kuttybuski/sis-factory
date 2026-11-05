"""Hybrid ranking helpers (pure)."""

import posixpath

from sis_context.models import Hit

RRF_K = 60


def rrf(rankings: list[list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal rank fusion: merges ranked id lists without needing comparable scores."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for pos, id_ in enumerate(ranking):
            scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + pos + 1)
    return scores


def path_overlap(hint: list[str], paths: list[str]) -> float:
    """1.0 if a PR touched one of the task's files, 0.5 if it touched the same directory, else 0."""
    if not hint or not paths:
        return 0.0
    files, dirs = set(hint), {posixpath.dirname(h) for h in hint}
    if any(p in files for p in paths):
        return 1.0
    return 0.5 if any(posixpath.dirname(p) in dirs for p in paths) else 0.0


def boost(hit: Hit, paths: list[str]) -> float:
    """Final score: fused score, plus a bonus for PRs near the task's files, minus a malus for flagged text."""
    b = hit.score
    if hit.kind == "pr" or hit.meta.get("doc_kind") == "pr":
        b *= 1.0 + 0.6 * path_overlap(hit.path_hint, paths)
    if hit.flagged:
        b *= 0.5
    return b


def dedupe_per_doc(hits: list[Hit], max_per_doc: int = 2) -> list[Hit]:
    seen: dict[int, int] = {}
    out = []
    for h in hits:
        if seen.get(h.doc_id, 0) < max_per_doc:
            out.append(h)
            seen[h.doc_id] = seen.get(h.doc_id, 0) + 1
    return out


def keyword_query(text: str, limit: int = 24) -> str:
    """OR-query for Postgres to_tsquery from free text: letters and digits only, so it cannot inject query syntax."""
    import re

    seen, words = set(), []
    for w in re.findall(r"[a-z0-9_]{3,}", text.lower()):
        if w not in seen:
            seen.add(w)
            words.append(w)
    return " | ".join(words[:limit])
