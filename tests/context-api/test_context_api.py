import os

import httpx
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")
TEAM = "ApiTeam"


@pytest.fixture(scope="module")
def client():
    from context_api.main import app

    with TestClient(app) as c:
        def portal(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/lessons/relevant"
            return httpx.Response(200, json={"rules": "## Team rules (approved by your team)\n- [L1] run the linter first\n", "lessons": [],
                                             "knowledge": "## Team knowledge (T, written by the team)\n### Non-negotiables\nNever log card numbers.\n"})

        app.state.portal = httpx.AsyncClient(base_url="http://portal", transport=httpx.MockTransport(portal))
        c.call = c.portal.call
        yield c


def index(client, docs):
    from sis_context import db
    from sis_context.models import Chunk, DocIn

    async def run(pool=client.app.state.pool, emb=client.app.state.embedder):
        src = await db.upsert_source(pool, kind="push", key=f"api-test-{docs[0][0]}", team=TEAM)

        async def gen():
            for kind, ext, title, text, extra in docs:
                yield DocIn(kind=kind, external_id=ext, title=title, url=f"https://x/{ext}", team=TEAM, chunks=[Chunk(text=text, heading_path=title)], **extra)

        return await db.sync_docs(pool, src["id"], gen(), emb)

    client.portal.call(run)


def test_pack_combines_rules_answers_prs_and_docs_with_citations(client):
    index(client, [("page", "p1", "Retry policy", "Failed charges are retried with exponential backoff and jitter.", {}),
                   ("pr", "pr1", "PR 12 add retry", "Added exponential backoff to the billing retry worker.", {"path_hint": ["src/billing/retry.py"]}),
                   ("qa", "q1", "Backoff?", "Question: Should retries use backoff or a fixed delay?\nAnswer: Exponential backoff.", {})])
    r = client.post("/v1/pack", json={"task_id": "API-1", "jira_key": "API-1", "title": "Add retry with backoff", "description": "billing worker retries",
                                      "team": TEAM, "paths": ["src/billing/retry.py"]}).json()
    md = r["markdown"]
    assert r["rules_included"] and r["lessons_ok"] and "[L1] run the linter first" in md
    assert md.index("Team knowledge") < md.index("Team rules") < md.index("Related merged pull requests") < md.index("Reference documentation")
    assert "Never log card numbers." in md  # what the team wrote down comes first, as trusted text
    assert "<untrusted source=" in md and {c["section"] for c in r["citations"]} >= {"pr", "docs"}
    used = client.get("/v1/usage/API-1").json()
    assert {u["section"] for u in used} >= {"pr", "docs"}


def test_pack_still_works_when_portal_api_is_down(client):
    from context_api.main import app

    app.state.portal = httpx.AsyncClient(base_url="http://portal", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    r = client.post("/v1/pack", json={"title": "Add retry with backoff", "team": TEAM}).json()
    assert r["lessons_ok"] is False and r["rules_included"] is False and "Team rules" not in r["markdown"]


def test_unanswered_topics_are_recorded_as_documentation_gaps(client):
    client.post("/v1/pack", json={"task_id": "API-2", "title": "Zxqv quuxplorp frobnicate", "team": "NobodyHasDocsForThis", "repo": "none/none"})
    assert any(g["query"] == "Zxqv quuxplorp frobnicate" for g in client.get("/v1/gaps").json())


def test_similar_questions_offers_earlier_answers_only_above_the_threshold(client):
    q = "Should retries use backoff or a fixed delay?"
    hit = client.post("/v1/similar-questions", json={"question": q, "team": TEAM, "threshold": 0.5}).json()["matches"]
    assert hit and hit[0]["answer"] == "Exponential backoff." and hit[0]["similarity"] >= 0.5
    assert client.post("/v1/similar-questions", json={"question": q, "team": TEAM, "threshold": 0.999}).json()["matches"] == []
    assert client.post("/v1/similar-questions", json={"question": q, "team": "OtherTeam", "threshold": 0.1}).json()["matches"] == []


def test_search_is_scoped_to_the_callers_team(client):
    mine = client.post("/v1/search", json={"query": "exponential backoff retries", "team": TEAM}).json()["hits"]
    theirs = client.post("/v1/search", json={"query": "exponential backoff retries", "team": "Stranger"}).json()["hits"]
    assert mine and not any(h["title"] == "Retry policy" for h in theirs)


def test_sources_and_health(client):
    assert client.get("/healthz").json()["ok"] is True
    assert any(s["key"].startswith("api-test") and s["docs"] >= 1 for s in client.get("/v1/sources").json())
