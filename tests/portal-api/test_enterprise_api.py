"""Enterprise controls in portal-api: the dashboard token, team leads, intake pauses, the append-only audit log, retention, key rotation."""

import os
import time

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")

SERVICE = {"Authorization": "Bearer test-token"}
DASH = {"Authorization": "Bearer dash-token"}


@pytest.fixture(scope="module")
def client():
    os.environ["SIF_INGEST_TOKEN"] = "test-token"
    from portal_api.main import app

    with TestClient(app) as c:
        yield c


def test_with_a_dashboard_token_services_cannot_make_peoples_decisions(client, monkeypatch):
    monkeypatch.setenv("SIF_DASHBOARD_TOKEN", "dash-token")
    team = {"name": "Enterprise", "jira_projects": ["ENT"], "repos": ["acme/ent"]}
    assert client.put("/api/teams", json=team, headers=SERVICE).status_code == 403
    assert client.put("/api/teams", json=team, headers=DASH).status_code == 200
    for method, path in [("post", "/api/lessons/1/approve"), ("post", "/api/tasks/X-1/kill"), ("put", "/api/intake/pause"),
                         ("put", "/api/teams/Enterprise/knowledge/rules"), ("post", "/api/improve/run")]:
        assert getattr(client, method)(path, headers=SERVICE).status_code == 403, path
    # what services do keeps working with their own token, and the dashboard can do it too
    ev = {"type": "task.discovered", "taskId": "ENT-1", "ts": int(time.time() * 1000), "jiraKey": "ENT-1", "title": "x"}
    assert client.post("/api/events", json=ev, headers=SERVICE).status_code == 202
    assert client.post("/api/lessons", json={"text": "Use the shared retry helper for HTTP calls.", "scope": "repo", "repo": "acme/ent", "author": "x"},
                       headers=DASH).status_code == 201
    assert client.put("/api/teams", json=team, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_team_leads_are_stored_lowercase_and_validated(client):
    r = client.put("/api/teams", headers=SERVICE, json={"name": "Enterprise", "jira_projects": ["ENT"], "repos": ["acme/ent"], "leads": ["Ann@Acme.com", "payments leads"]})
    assert r.status_code == 200 and r.json()["leads"] == ["ann@acme.com", "payments leads"]
    assert client.put("/api/teams", headers=SERVICE, json={"name": "Enterprise", "leads": ["<script>"]}).status_code == 422


def test_intake_can_be_paused_for_one_team_or_all_and_reports_counts(client):
    client.put("/api/teams", headers=SERVICE, json={"name": "Enterprise", "jira_projects": ["ENT"], "repos": ["acme/ent"]})
    assert client.put("/api/intake/pause", headers=SERVICE, json={"team": "Nope", "by": "rae"}).status_code == 404
    assert client.put("/api/intake/pause", headers=SERVICE, json={"team": "Enterprise", "reason": "incident", "by": "rae"}).status_code == 200
    body = client.get("/api/intake").json()
    assert not body["paused_all"] and [p["team"] for p in body["pauses"]] == ["Enterprise"] and body["pauses"][0]["reason"] == "incident"
    assert body["held"]["Enterprise"] == "intake is paused for Enterprise by rae (incident)"  # the reason jira-connector and the 3D view show
    assert body["limits"] == {"max_running": 0, "max_daily": 25}
    assert client.put("/api/intake/pause", headers=SERVICE, json={"team": "*", "by": "rae"}).status_code == 200
    assert client.get("/api/intake").json()["paused_all"]
    for team in ("Enterprise", "*"):
        assert client.delete(f"/api/intake/pause/{team}", headers=SERVICE).status_code == 200
    assert client.delete("/api/intake/pause/Enterprise", headers=SERVICE).status_code == 404
    ev = {"type": "task.discovered", "taskId": "ENT-77", "ts": int(time.time() * 1000), "jiraKey": "ENT-77", "title": "x"}
    client.post("/api/events", json=ev, headers=SERVICE)
    assert client.get("/api/intake").json()["counts"]["Enterprise"]["started_24h"] >= 1


def test_a_team_at_its_daily_limit_is_reported_as_held(client, monkeypatch):
    monkeypatch.setenv("SIF_MAX_TASKS_PER_TEAM_PER_DAY", "1")
    client.put("/api/teams", headers=SERVICE, json={"name": "Capped", "jira_projects": ["CAP"], "repos": ["acme/cap"]})
    client.post("/api/events", headers=SERVICE, json={"type": "task.discovered", "taskId": "CAP-1", "ts": int(time.time() * 1000), "jiraKey": "CAP-1", "team": "Capped"})
    body = client.get("/api/intake").json()
    assert body["limits"]["max_daily"] == 1 and "24 hours" in body["held"]["Capped"]


def test_the_audit_log_is_append_only_except_for_retention(client):
    import asyncio

    from portal_api import db
    from portal_api.main import app

    client.put("/api/intake/pause", headers=SERVICE, json={"team": "*", "by": "rae"})
    client.delete("/api/intake/pause/*", headers=SERVICE)

    async def tamper(sql):
        async with app.state.pool.connection() as conn:
            await conn.execute(sql)

    for sql in ("update audit_log set actor = 'nobody'", "delete from audit_log"):
        with pytest.raises(Exception, match="append-only"):
            asyncio.run(tamper(sql))

    async def backdate_and_purge():
        async with app.state.pool.connection() as conn:
            await conn.execute("alter table audit_log disable trigger audit_log_append_only")
            await conn.execute("update audit_log set at = now() - interval '400 days' where id = (select min(id) from audit_log)")
            await conn.execute("alter table audit_log enable trigger audit_log_append_only")
        return await db.purge(app.state.pool, event_days=0, audit_days=365)

    assert asyncio.run(backdate_and_purge())["audit"] == 1


def test_retention_deletes_old_events_of_finished_tasks_only(client):
    import asyncio

    from portal_api import db
    from portal_api.main import app

    old = int((time.time() - 100 * 86400) * 1000)
    for task, final in (("KEEP-1", True), ("KEEP-2", False)):
        client.post("/api/events", headers=SERVICE, json={"type": "task.discovered", "taskId": task, "ts": old, "jiraKey": task, "title": "t"})
        if final:
            client.post("/api/events", headers=SERVICE, json={"type": "task.failed", "taskId": task, "ts": old + 1000})
    asyncio.run(db.purge(app.state.pool, event_days=30, audit_days=0))
    assert client.get("/api/tasks/KEEP-1").json()["events"] == [] and client.get("/api/tasks/KEEP-1").json()["task"]["outcome"] == "failed"
    assert len(client.get("/api/tasks/KEEP-2").json()["events"]) == 1  # still running: kept


def test_rotating_the_secret_key_keeps_saved_secrets_readable_and_moves_them(client, monkeypatch):
    import asyncio

    from cryptography.fernet import Fernet

    from portal_api import db, vault
    from portal_api.main import app

    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv("SIF_SECRET_KEY", old)
    client.put("/api/teams", headers=SERVICE, json={"name": "Enterprise", "jira_projects": ["ENT"]})
    asyncio.run(db.put_connection(app.state.pool, team="Enterprise", provider="github", config={}, secret=vault.encrypt("ghp_rotate_me"), hint="x", by="t"))
    monkeypatch.setenv("SIF_SECRET_KEY", new)
    monkeypatch.setenv("SIF_SECRET_KEYS_OLD", old)
    assert asyncio.run(vault.reencrypt(app.state.pool)) >= 1
    monkeypatch.delenv("SIF_SECRET_KEYS_OLD")
    row = asyncio.run(db.get_connection_secret(app.state.pool, "Enterprise", "github"))
    assert vault.decrypt(row["secret"]) == "ghp_rotate_me"  # readable with the new key alone
    assert asyncio.run(vault.reencrypt(app.state.pool)) == 0


def test_posture_is_empty_without_git_broker(client, monkeypatch):
    monkeypatch.delenv("GIT_BROKER_URL", raising=False)
    client.put("/api/teams", headers=SERVICE, json={"name": "Enterprise", "jira_projects": ["ENT"], "repos": ["acme/ent"]})
    assert client.get("/api/teams/Enterprise/posture").json() == []
    assert client.get("/api/teams/Nope/posture").status_code == 404
