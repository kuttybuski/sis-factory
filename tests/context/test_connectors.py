import base64

import httpx
import pytest
from sif_context import confluence, github

BASE = "https://acme.atlassian.net"


def confluence_transport():
    pages1 = {"results": [{"id": "1", "title": "Engineering", "version": {"number": 3}, "body": {"storage": {"value": "<h1>Eng home</h1><p>Welcome to engineering docs and standards for every team.</p>"}}, "_links": {"webui": "/spaces/ENG/pages/1"}}],
              "_links": {"next": "/wiki/api/v2/spaces/10/pages?cursor=abc"}}
    pages2 = {"results": [{"id": "2", "title": "Payments", "parentId": "1", "version": {"number": 7},
                           "body": {"storage": {"value": "<h2>Retries</h2><p>Failed charges are retried with exponential backoff at most five times in total.</p>"}}, "_links": {"webui": "/spaces/ENG/pages/2"}},
                          {"id": "3", "title": "Salaries", "parentId": "1", "version": {"number": 1}, "body": {"storage": {"value": "<p>Restricted page about pay bands for everyone.</p>"}}},
                          {"id": "4", "title": "Bands 2026", "parentId": "3", "version": {"number": 1}, "body": {"storage": {"value": "<p>Child of a restricted page inherits it.</p>"}}}]}
    restricted = {"user": {"results": [{"accountId": "hr"}]}, "group": {"results": []}}
    v1 = [{"results": [{"id": "1", "restrictions": {"read": {"restrictions": {"user": {"results": []}, "group": {"results": []}}}}}], "_links": {"next": "/rest/api/content?cursor=n"}},
          {"results": [{"id": "3", "restrictions": {"read": {"restrictions": restricted}}}]}]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/wiki/api/v2/spaces":
            return httpx.Response(200, json={"results": [{"id": "10", "key": req.url.params["keys"]}] if req.url.params["keys"] == "ENG" else []})
        if req.url.path == "/wiki/api/v2/spaces/10/pages":
            assert req.url.params.get("body-format") in ("storage", None)
            return httpx.Response(200, json=pages2 if req.url.params.get("cursor") == "abc" else pages1)
        if req.url.path == "/wiki/rest/api/content":
            return httpx.Response(200, json=v1[1] if req.url.params.get("cursor") == "n" else v1[0])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_confluence_follows_pagination_and_builds_breadcrumbs():
    c = httpx.AsyncClient(base_url=BASE, transport=confluence_transport())
    docs = [d async for d in confluence.fetch_space(c, "ENG", team="Payments")]
    assert [d.external_id for d in docs] == ["1", "2"] and all(d.team == "Payments" for d in docs)  # 3 is restricted, 4 inherits it
    child = docs[1]
    assert child.version == "7" and child.url == f"{BASE}/wiki/spaces/ENG/pages/2"
    assert child.chunks[0].heading_path.startswith("ENG > Engineering > Payments")


@pytest.mark.asyncio
async def test_restricted_pages_are_indexed_only_when_asked():
    c = httpx.AsyncClient(base_url=BASE, transport=confluence_transport())
    assert [d.external_id async for d in confluence.fetch_space(c, "ENG", skip_restricted=False)] == ["1", "2", "3", "4"]


@pytest.mark.asyncio
async def test_confluence_missing_space_is_a_clear_error():
    c = httpx.AsyncClient(base_url=BASE, transport=confluence_transport())
    with pytest.raises(LookupError, match="NOPE"):
        [d async for d in confluence.fetch_space(c, "NOPE")]


def github_transport(rate_limited=False):
    def pr(n, merged):
        return {"number": n, "title": f"PAY-{n} change", "body": "", "html_url": f"https://github.com/acme/api/pull/{n}", "user": {"login": "ann"},
                "merged_at": merged, "labels": [], "head": {"ref": f"feature/PAY-{n}"}}

    closed = [pr(5, "2026-05-05T00:00:00Z"), pr(4, None), pr(3, "2026-05-03T00:00:00Z"), pr(2, "2026-05-02T00:00:00Z")]

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        assert req.headers["authorization"] == "Bearer tok"
        if rate_limited:
            return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "123"})
        if path == "/repos/acme/api/pulls":
            return httpx.Response(200, json=closed)
        if path.endswith("/files"):
            return httpx.Response(200, json=[{"filename": "src/billing/retry.py", "status": "modified", "additions": 5, "deletions": 1, "patch": "@@\n+x"}])
        if path.endswith("/pulls/5/comments"):
            return httpx.Response(200, json=[{"id": 1, "user": {"login": "bob"}, "body": "Please use backoff instead of a fixed delay.", "path": "src/billing/retry.py", "diff_hunk": "@@\n+sleep(5)"}])
        if path.endswith("/comments") or path.endswith("/reviews"):
            return httpx.Response(200, json=[])
        if path == "/repos/acme/api":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/acme/api/git/trees/main":
            return httpx.Response(200, json={"tree": [
                {"path": "README.md", "type": "blob", "size": 100}, {"path": "AGENTS.md", "type": "blob", "size": 50}, {"path": "docs/adr/0001-queues.md", "type": "blob", "size": 80},
                {"path": "src/app.py", "type": "blob", "size": 10}, {"path": "docs/huge.md", "type": "blob", "size": 9_999_999}, {"path": "docs", "type": "tree"}]})
        if path.startswith("/repos/acme/api/contents/"):
            body = "# Title\n\nSome documentation text that is long enough to keep around in the index.\n"
            return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(body.encode()).decode(), "sha": "abc", "html_url": "https://github.com/x"})
        return httpx.Response(404, json={"path": path})

    return httpx.MockTransport(handler)


def gh(transport):
    return httpx.AsyncClient(base_url=github.API, headers={"Authorization": "Bearer tok"}, transport=transport)


@pytest.mark.asyncio
async def test_only_merged_prs_newest_first_up_to_the_limit():
    prs = await github.merged_prs(gh(github_transport()), "acme/api", limit=2)
    assert [p["number"] for p in prs] == [5, 3]


@pytest.mark.asyncio
async def test_prs_become_documents_with_files_and_threads():
    docs = [d async for d in github.fetch_prs(gh(github_transport()), "acme/api", team="Payments", limit=10)]
    assert sorted(d.external_id for d in docs) == ["2", "3", "5"]
    five = next(d for d in docs if d.external_id == "5")
    assert five.path_hint == ["src/billing/retry.py"] and five.repo == "acme/api" and five.team == "Payments"
    assert any(c.kind == "thread" and "backoff" in c.text for c in five.chunks)


@pytest.mark.asyncio
async def test_repo_docs_pick_only_documentation_files_within_size_limits():
    docs = [d async for d in github.fetch_repo_docs(gh(github_transport()), "acme/api")]
    assert sorted(d.external_id for d in docs) == ["AGENTS.md", "README.md", "docs/adr/0001-queues.md"]
    assert all(d.chunks and d.repo == "acme/api" for d in docs)


@pytest.mark.asyncio
async def test_rate_limits_are_reported_not_swallowed():
    with pytest.raises(github.RateLimited, match="123"):
        await github.merged_prs(gh(github_transport(rate_limited=True)), "acme/api")
