"""GitHub connector: merged pull requests and repository documentation. Read-only."""

import asyncio
import base64
import fnmatch
from collections.abc import AsyncIterator

import httpx

from sis_context.models import DocIn
from sis_context.pr import Comment, FileChange, PRData, ReviewComment, pr_to_doc
from sis_context.text import md_to_chunks

API = "https://api.github.com"
DOC_GLOBS = ["README*", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING*", "ARCHITECTURE*", "CODEOWNERS", ".github/CODEOWNERS", "docs/**/*.md", "docs/*.md", "adr/**/*.md", "doc/adr/**/*.md"]
MAX_DOC_BYTES, MAX_DOCS = 200_000, 200


class RateLimited(Exception):
    def __init__(self, reset: str) -> None:
        super().__init__(f"GitHub rate limit reached; resets at epoch {reset}")


def client(token: str, base_url: str = API) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, timeout=60, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})


async def _get(c: httpx.AsyncClient, url: str, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
    r = await c.get(url, params=params, headers=headers)
    if r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
        raise RateLimited(r.headers.get("x-ratelimit-reset", "?"))
    r.raise_for_status()
    return r


async def _list(c: httpx.AsyncClient, url: str, params: dict | None = None, max_pages: int = 5) -> list[dict]:
    out, page = [], 1
    while page <= max_pages:
        items = (await _get(c, url, {**(params or {}), "per_page": 100, "page": page})).json()
        out.extend(items)
        if len(items) < 100:
            break
        page += 1
    return out


async def merged_prs(c: httpx.AsyncClient, repo: str, limit: int = 100) -> list[dict]:
    """The most recently merged PRs. The list endpoint returns closed PRs, so unmerged ones are skipped."""
    found, page = [], 1
    while len(found) < limit and page <= 20:
        items = (await _get(c, f"/repos/{repo}/pulls", {"state": "closed", "sort": "updated", "direction": "desc", "per_page": 100, "page": page})).json()
        found.extend(p for p in items if p.get("merged_at"))
        if len(items) < 100:
            break
        page += 1
    found.sort(key=lambda p: p["merged_at"], reverse=True)
    return found[:limit]


async def fetch_pr(c: httpx.AsyncClient, repo: str, p: dict) -> PRData:
    n = p["number"]
    files, rcs, ics, reviews = await asyncio.gather(
        _list(c, f"/repos/{repo}/pulls/{n}/files", max_pages=3), _list(c, f"/repos/{repo}/pulls/{n}/comments", max_pages=3),
        _list(c, f"/repos/{repo}/issues/{n}/comments", max_pages=2), _list(c, f"/repos/{repo}/pulls/{n}/reviews", max_pages=2))
    who = lambda x: (x.get("user") or {}).get("login", "")  # noqa: E731
    return PRData(
        repo=repo, number=n, title=p["title"], body=p.get("body") or "", url=p.get("html_url", ""), author=who(p), merged_at=p.get("merged_at", ""),
        labels=[l["name"] for l in p.get("labels", [])], head_ref=(p.get("head") or {}).get("ref", ""),
        files=[FileChange(path=f["filename"], status=f.get("status", "modified"), additions=f.get("additions", 0), deletions=f.get("deletions", 0), patch=f.get("patch", "")) for f in files],
        review_comments=[ReviewComment(id=x["id"], author=who(x), body=x.get("body", ""), path=x.get("path", ""), diff_hunk=x.get("diff_hunk", ""), in_reply_to=x.get("in_reply_to_id"),
                                       association=x.get("author_association", "")) for x in rcs],
        comments=[Comment(author=who(x), body=x.get("body", ""), association=x.get("author_association", "")) for x in ics]
        + [Comment(author=who(x), body=x.get("body") or "", state=x.get("state", ""), association=x.get("author_association", "")) for x in reviews if x.get("body")])


async def fetch_prs(c: httpx.AsyncClient, repo: str, team: str | None = None, limit: int = 100, concurrency: int = 4,
                    collect: list[PRData] | None = None) -> AsyncIterator[DocIn]:
    sem = asyncio.Semaphore(concurrency)

    async def one(p: dict) -> DocIn:
        async with sem:
            data = await fetch_pr(c, repo, p)
            if collect is not None:
                collect.append(data)
            return pr_to_doc(data, team)

    for fut in asyncio.as_completed([asyncio.create_task(one(p)) for p in await merged_prs(c, repo, limit)]):
        yield await fut


async def fetch_repo_docs(c: httpx.AsyncClient, repo: str, team: str | None = None, ref: str | None = None) -> AsyncIterator[DocIn]:
    if ref is None:
        ref = (await _get(c, f"/repos/{repo}")).json()["default_branch"]
    tree = (await _get(c, f"/repos/{repo}/git/trees/{ref}", {"recursive": "1"})).json().get("tree", [])
    paths = [t["path"] for t in tree if t["type"] == "blob" and t.get("size", 0) <= MAX_DOC_BYTES and any(fnmatch.fnmatch(t["path"], g) or fnmatch.fnmatch(t["path"].split("/")[-1], g) for g in DOC_GLOBS)]
    for path in paths[:MAX_DOCS]:
        r = await _get(c, f"/repos/{repo}/contents/{path}", {"ref": ref}, headers={"Accept": "application/vnd.github+json"})
        data = r.json()
        text = base64.b64decode(data["content"]).decode("utf-8", "replace") if data.get("encoding") == "base64" else ""
        yield DocIn(kind="doc", external_id=path, title=path, url=data.get("html_url", ""), version=data.get("sha", ""), repo=repo, team=team,
                    meta={"path": path}, chunks=md_to_chunks(text, path, repo))
