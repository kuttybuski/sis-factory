"""Per-team connection API (needs DATABASE_URL). The Jira and GitHub checks run against a mock transport."""

import os

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")
AUTH = {"Authorization": "Bearer test-token"}
INTERNAL = {"Authorization": "Bearer internal-token"}
TEAM = "Conn-Team"
TOKEN = "jira-secret-token-1234"
GH = "ghp_" + "Zz9y" * 9


class Upstream:
    """What Jira and GitHub say. Tests change the fields to simulate bad tokens and missing access."""

    def __init__(self):
        self.jira_ok, self.visible, self.github_ok, self.readable, self.seen = True, {"CNX"}, True, {"acme/api"}, []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.seen.append((str(req.url), req.headers.get("authorization", "")))
        path = req.url.path
        if path == "/rest/api/3/myself":
            return httpx.Response(200, json={"displayName": "Sif Bot"}) if self.jira_ok else httpx.Response(401)
        if path.startswith("/rest/api/3/project/"):
            return httpx.Response(200 if path.rsplit("/", 1)[1] in self.visible else 404)
        if path == "/user":
            return httpx.Response(200, json={"login": "sif-bot"}) if self.github_ok else httpx.Response(401)
        if path.startswith("/repos/"):
            return httpx.Response(200 if path.removeprefix("/repos/") in self.readable else 404)
        return httpx.Response(404)


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setenv("SIF_INGEST_TOKEN", "test-token")
    mp.setenv("SIF_INTERNAL_TOKEN", "internal-token")
    mp.setenv("SIF_SECRET_KEY", Fernet.generate_key().decode())
    mp.setenv("SIF_ALLOW_PRIVATE_URLS", "true")
    from portal_api.main import app

    with TestClient(app) as c:
        c.put("/api/teams", headers=AUTH, json={"name": TEAM, "jira_projects": ["CNX"], "repos": ["acme/api"]})
        yield c
    mp.undo()


@pytest.fixture()
def up(monkeypatch):
    from portal_api import connection_check

    u = Upstream()
    monkeypatch.setattr(connection_check, "client_factory", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(u.handler), **kw))
    return u


@pytest.fixture(autouse=True)
def clean(client):
    for provider in ("jira", "github"):
        client.delete(f"/api/connections/{TEAM}/{provider}", headers=AUTH)


JIRA = {"config": {"base_url": "https://jira.test", "email": "bot@acme.test"}, "secret": TOKEN}


def put(client, provider, body, **kw):
    return client.put(f"/api/connections/{TEAM}/{provider}", json=body, headers=kw.get("headers", AUTH))


def row(client, provider):
    return next((c for c in client.get("/api/connections").json()["connections"] if c["team"] == TEAM and c["provider"] == provider), None)


def test_a_good_jira_connection_is_saved_checked_and_shown_without_its_secret(client, up):
    r = put(client, "jira", JIRA)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "connected" and "Signed in as Sif Bot" in body["detail"] and "CNX" in body["detail"] and body["checked_at"]
    assert body["secret_hint"] == "••••1234" and body["config"] == JIRA["config"]
    everything = client.get("/api/connections").text + r.text
    assert TOKEN not in everything and all("secret" not in c for c in client.get("/api/connections").json()["connections"])
    assert any(auth.startswith("Basic ") for _, auth in up.seen)  # the check used the stored credentials


def test_bad_credentials_and_missing_projects_are_reported_not_hidden(client, up):
    up.jira_ok = False
    assert put(client, "jira", JIRA).json()["status"] == "failed"
    assert "rejected" in row(client, "jira")["detail"]
    up.jira_ok, up.visible = True, set()
    out = client.post(f"/api/connections/{TEAM}/jira/test", headers=AUTH).json()
    assert out["status"] == "failed" and "cannot see the project(s): CNX" in out["detail"]


def test_github_checks_the_token_and_the_teams_repos(client, up):
    ok = put(client, "github", {"secret": GH}).json()
    assert ok["status"] == "connected" and "sif-bot" in ok["detail"] and "acme/api" in ok["detail"]
    up.readable = set()
    bad = client.post(f"/api/connections/{TEAM}/github/test", headers=AUTH).json()
    assert bad["status"] == "failed" and "cannot read: acme/api" in bad["detail"]
    up.github_ok = False
    assert "rejected" in client.post(f"/api/connections/{TEAM}/github/test", headers=AUTH).json()["detail"]
    assert GH not in client.get("/api/connections").text


def test_writes_need_the_service_token_and_reading_a_secret_needs_the_internal_one(client, up):
    assert put(client, "jira", JIRA, headers={}).status_code == 401
    assert client.delete(f"/api/connections/{TEAM}/jira").status_code == 401
    put(client, "jira", JIRA)
    assert client.get(f"/api/internal/connections/{TEAM}/jira").status_code == 401
    assert client.get(f"/api/internal/connections/{TEAM}/jira", headers=AUTH).status_code == 401  # the ingest token cannot read secrets
    got = client.get(f"/api/internal/connections/{TEAM}/jira", headers=INTERNAL).json()
    assert got == {"config": JIRA["config"], "secret": TOKEN}


def test_a_team_without_a_connection_is_a_404_for_the_services(client):
    assert client.get(f"/api/internal/connections/{TEAM}/jira", headers=INTERNAL).status_code == 404
    assert row(client, "jira") is None


def test_the_token_can_be_left_blank_to_keep_it_but_not_when_the_address_changes(client, up):
    put(client, "jira", JIRA)
    kept = put(client, "jira", {"config": {**JIRA["config"], "email": "bot@acme.test"}, "secret": ""})
    assert kept.status_code == 200 and kept.json()["secret_hint"] == "••••1234"
    moved = put(client, "jira", {"config": {"base_url": "https://evil.test", "email": "bot@acme.test"}})
    assert moved.status_code == 422 and "enter the token again" in moved.json()["detail"]
    assert client.get(f"/api/internal/connections/{TEAM}/jira", headers=INTERNAL).json()["config"]["base_url"] == "https://jira.test"
    assert put(client, "jira", {"config": {"base_url": "https://new.test", "email": "bot@acme.test"}, "secret": "fresh-token-5678"}).json()["secret_hint"] == "••••5678"


def test_input_is_validated(client, up):
    assert put(client, "jira", {"config": {"base_url": "https://jira.test"}, "secret": TOKEN}).status_code == 422  # email missing
    assert put(client, "jira", {**JIRA, "secret": "two words"}).status_code == 422
    assert put(client, "jira", {"config": {**JIRA["config"], "colour": "red"}, "secret": TOKEN}).status_code == 422
    assert put(client, "jira", {"config": JIRA["config"]}).status_code == 422  # no token at all
    assert put(client, "gitlab", JIRA).status_code == 404
    assert client.put("/api/connections/Nobody/jira", json=JIRA, headers=AUTH).status_code == 404


def test_private_addresses_are_refused_unless_allowed(client, up, monkeypatch):
    monkeypatch.setenv("SIF_ALLOW_PRIVATE_URLS", "false")
    r = put(client, "jira", {"config": {"base_url": "https://127.0.0.1", "email": "b@a.test"}, "secret": TOKEN})
    assert r.status_code == 422 and "private address" in r.json()["detail"]
    assert put(client, "jira", {"config": {"base_url": "http://93.184.216.34", "email": "b@a.test"}, "secret": TOKEN}).status_code == 422  # http


def test_without_a_vault_key_nothing_is_stored(client, up, monkeypatch):
    monkeypatch.delenv("SIF_SECRET_KEY")
    r = put(client, "jira", JIRA)
    assert r.status_code == 503 and "SIF_SECRET_KEY" in r.json()["detail"] and row(client, "jira") is None
    assert client.get("/api/connections").json()["vault"] is False


def test_a_changed_key_is_reported_as_a_failed_connection_not_a_crash(client, up, monkeypatch):
    put(client, "jira", JIRA)
    monkeypatch.setenv("SIF_SECRET_KEY", Fernet.generate_key().decode())
    out = client.post(f"/api/connections/{TEAM}/jira/test", headers=AUTH).json()
    assert out["status"] == "failed" and "SIF_SECRET_KEY" in out["detail"]
    assert client.get(f"/api/internal/connections/{TEAM}/jira", headers=INTERNAL).status_code == 500


def test_removing_a_connection_returns_the_team_to_the_shared_credentials(client, up):
    put(client, "github", {"secret": GH})
    assert client.delete(f"/api/connections/{TEAM}/github", headers=AUTH).json() == {"removed": True}
    assert client.get(f"/api/internal/connections/{TEAM}/github", headers=INTERNAL).status_code == 404
