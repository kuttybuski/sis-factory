"""The knowledge PR loop against fake portal-api and git-broker endpoints."""

import json

import httpx
import pytest

from orchestrator_worker.digests import check_open_digests, open_due_digests

pytestmark = pytest.mark.asyncio
BASE = "b" * 40


def fakes(files: dict[tuple[str, str], str], pr_state: dict | None = None, publish_status: int = 201):
    seen: list[tuple[str, str, dict]] = []

    def portal(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        seen.append(("portal", req.url.path, body))
        if req.url.path == "/api/digests/claim":
            return httpx.Response(200, json=[{"id": 5, "repo": "acme/api", "team": "Payments", "lessons": [{"id": 11, "text": "Use Decimal for money.", "source": "mined"}]}])
        if req.url.path == "/api/digests":
            return httpx.Response(200, json=[{"id": 5, "repo": "acme/api", "team": "Payments", "pr_number": 9, "pr_url": "https://x/pull/9"}])
        return httpx.Response(200, json={})

    def broker(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        seen.append(("broker", req.url.path, body))
        if req.url.path == "/v1/checkout":
            return httpx.Response(200, json={"commit": BASE, "ref": "main"})
        if req.url.path == "/v1/file":
            content = files.get((body["commit"], body["path"]))
            return httpx.Response(200, json={"content": content}) if content is not None else httpx.Response(404, json={})
        if req.url.path == "/v1/publish":
            return httpx.Response(publish_status, json={"number": 9, "pr_url": "https://x/pull/9", "branch": body["branch"]})
        if req.url.path.startswith("/v1/pr/"):
            return httpx.Response(200, json=pr_state)
        return httpx.Response(404)

    return (httpx.AsyncClient(base_url="http://portal", transport=httpx.MockTransport(portal)),
            httpx.AsyncClient(base_url="http://broker", transport=httpx.MockTransport(broker)), seen)


async def test_a_due_repository_gets_one_pr_that_adds_its_lessons_to_claude_md():
    portal, broker, seen = fakes({(BASE, "CLAUDE.md"): "# Project\n"})
    assert await open_due_digests(portal, broker) == 1
    pub = next(b for who, path, b in seen if path == "/v1/publish")
    assert pub["branch"].startswith("sis/knowledge-") and pub["branch"].endswith("-5") and pub["base_commit"] == BASE and pub["team"] == "Payments"
    assert "+- Use Decimal for money. _(sis L11)_" in pub["patch"] and "--- a/CLAUDE.md" in pub["patch"] and not pub["draft"]
    assert "1 lesson learned in acme/api" in pub["title"] and "**L11**" in pub["body"]
    assert ("portal", "/api/digests/5/opened", {"pr_number": 9, "pr_url": "https://x/pull/9", "branch": pub["branch"]}) in seen


async def test_a_repository_without_claude_md_gets_the_file_created():
    portal, broker, seen = fakes({})
    await open_due_digests(portal, broker)
    assert "new file mode 100644" in next(b for _, path, b in seen if path == "/v1/publish")["patch"]


async def test_a_pr_that_cannot_be_opened_puts_the_lessons_back():
    portal, broker, seen = fakes({}, publish_status=422)
    assert await open_due_digests(portal, broker) == 0
    failed = next(b for _, path, b in seen if path == "/api/digests/5/decided")
    assert failed["state"] == "failed" and "422" in failed["error"]


async def test_a_merged_pr_reports_which_lessons_survived_review():
    head = "h" * 40
    merged = "# Project\n<!-- sis:lessons:start -->\n- kept _(sis L11)_\n<!-- sis:lessons:end -->\n"
    portal, broker, seen = fakes({(head, "CLAUDE.md"): merged}, pr_state={"state": "merged", "merged_by": "rae", "head_sha": head})
    assert await check_open_digests(portal, broker) == 1
    assert ("portal", "/api/digests/5/decided", {"state": "merged", "merged_by": "rae", "kept_ids": [11]}) in seen


async def test_an_open_pr_is_left_alone():
    portal, broker, seen = fakes({}, pr_state={"state": "open", "head_sha": BASE})
    assert await check_open_digests(portal, broker) == 0 and not [p for _, p, _ in seen if p.endswith("/decided")]
