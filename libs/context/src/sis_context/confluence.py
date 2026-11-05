"""Confluence Cloud (REST v2) connector. Read-only; only the spaces it is given are touched.

Pages with read restrictions are skipped, and so is everything under them (restrictions are inherited). The connector reads as one
account, so a restricted page it can see would otherwise be searchable by every task of the team, and could end up quoted in a PR that
people without access read. SIS_CONFLUENCE_RESTRICTED=index keeps them (only for spaces whose readers all may see everything)."""

from collections.abc import AsyncIterator

import httpx

from sis_context.models import DocIn
from sis_context.text import html_to_chunks

PAGE_LIMIT = 50
MAX_PAGES_PER_SPACE = 5000


def client(base_url: str, email: str, token: str) -> httpx.AsyncClient:
    """base_url is the site root, for example https://acme.atlassian.net"""
    return httpx.AsyncClient(base_url=base_url.rstrip("/"), auth=(email, token), timeout=60, headers={"Accept": "application/json"})


async def _paged(c: httpx.AsyncClient, url: str, params: dict | None = None) -> AsyncIterator[dict]:
    """Follows Confluence's `_links.next` cursor."""
    nxt: str | None = url
    first = params
    while nxt:
        r = await c.get(nxt, params=first)
        r.raise_for_status()
        body = r.json()
        for item in body.get("results", []):
            yield item
        nxt = body.get("_links", {}).get("next")
        first = None  # the next link already carries the query


async def restricted_ids(c: httpx.AsyncClient, space_key: str) -> set[str]:
    """Ids of the space's pages that carry their own read restriction (v1 API: v2 does not expose restrictions in bulk)."""
    out: set[str] = set()
    nxt: str | None = "/wiki/rest/api/content"
    params: dict | None = {"spaceKey": space_key, "type": "page", "limit": 100,
                           "expand": "restrictions.read.restrictions.user,restrictions.read.restrictions.group"}
    pages = 0
    while nxt and pages < MAX_PAGES_PER_SPACE // 100 + 1:
        r = await c.get(nxt, params=params)
        r.raise_for_status()  # restrictions that cannot be read fail the sync: indexing blind could expose restricted pages
        body = r.json()
        for p in body.get("results", []):
            read = ((p.get("restrictions") or {}).get("read") or {}).get("restrictions") or {}
            if (read.get("user") or {}).get("results") or (read.get("group") or {}).get("results"):
                out.add(str(p["id"]))
        nxt = body.get("_links", {}).get("next")
        if nxt and nxt.startswith("/rest/"):
            nxt = "/wiki" + nxt  # v1 next links are relative to /wiki
        params, pages = None, pages + 1
    return out


async def fetch_space(c: httpx.AsyncClient, space_key: str, team: str | None = None, skip_restricted: bool = True) -> AsyncIterator[DocIn]:
    r = await c.get("/wiki/api/v2/spaces", params={"keys": space_key})
    r.raise_for_status()
    spaces = r.json().get("results", [])
    if not spaces:
        raise LookupError(f"Confluence space '{space_key}' not found or not visible to this account")
    space_id = spaces[0]["id"]

    pages = {}
    async for p in _paged(c, f"/wiki/api/v2/spaces/{space_id}/pages", {"body-format": "storage", "limit": PAGE_LIMIT, "status": "current"}):
        pages[p["id"]] = p
        if len(pages) >= MAX_PAGES_PER_SPACE:
            break

    def breadcrumb(page: dict) -> str:
        names, seen, pid = [], set(), page.get("parentId")
        while pid and pid in pages and pid not in seen:
            seen.add(pid)
            names.append(pages[pid]["title"])
            pid = pages[pid].get("parentId")
        return " > ".join([space_key, *reversed(names)])

    restricted = await restricted_ids(c, space_key) if skip_restricted else set()

    def hidden(page: dict) -> bool:
        seen, pid = set(), page["id"]
        while pid and pid not in seen:
            if str(pid) in restricted:
                return True
            seen.add(pid)
            pid = (pages.get(pid) or {}).get("parentId")
        return False

    base = str(c.base_url).rstrip("/")
    for p in pages.values():
        if hidden(p):
            continue
        body = (p.get("body") or {}).get("storage", {}).get("value", "")
        webui = (p.get("_links") or {}).get("webui", "")
        yield DocIn(kind="page", external_id=str(p["id"]), title=p["title"], url=f"{base}/wiki{webui}" if webui else "",
                    version=str((p.get("version") or {}).get("number", "")), team=team,
                    meta={"space": space_key, "parent_id": p.get("parentId")},
                    chunks=html_to_chunks(body, p["title"], breadcrumb(p)))
