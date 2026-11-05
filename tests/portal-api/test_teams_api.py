"""Team API: validation, the Temporal namespace registered on save, and delete (needs DATABASE_URL, like test_api.py).
Temporal is replaced by a recorder, so these run without a Temporal server."""

import os
import time

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")
AUTH = {"Authorization": "Bearer test-token"}


class FakeTemporal:
    def __init__(self):
        self.ensured, self.down = [], False

    async def ensure(self, namespace):
        if self.down:
            raise ConnectionError("temporal is down")
        created = namespace not in self.ensured
        self.ensured.append(namespace)
        return created


@pytest.fixture(scope="module")
def client():
    os.environ["SIF_INGEST_TOKEN"] = "test-token"
    from portal_api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def temporal(client):
    t = FakeTemporal()
    client.app.state.namespaces = t
    yield t
    del client.app.state.namespaces


def put(client, **team):
    return client.put("/api/teams", headers=AUTH, json=team)


def names(client):
    return {t["name"]: t for t in client.get("/api/teams").json()}


def event(client, kind, key, **kw):
    return client.post("/api/events", headers=AUTH, json={"type": kind, "taskId": key, "jiraKey": key, "ts": int(time.time() * 1000), **kw})


def test_saving_a_team_registers_its_namespace_and_normalises_the_lists(client, temporal):
    r = put(client, name="Billing Ops", jira_projects=["bil", " BIL ", ""], repos=["acme/billing"])
    assert r.status_code == 200, r.text
    assert (r.json()["temporal_namespace"], r.json()["namespace_created"]) == ("billing-ops", True) and temporal.ensured == ["billing-ops"]
    assert names(client)["Billing Ops"]["jira_projects"] == ["BIL"]
    assert put(client, name="Billing Ops", jira_projects=["BIL"]).json()["namespace_created"] is False


@pytest.mark.parametrize("team, field", [
    ({"name": ""}, "name"),
    ({"name": "!!!"}, "name"),
    ({"name": "x" * 61}, "name"),
    ({"name": "Temporal System"}, "name"),
    ({"name": "Proj", "jira_projects": ["PAY-1"]}, "jira_projects"),
    ({"name": "Repo", "repos": ["not a repo"]}, "repos"),
])
def test_bad_values_are_refused(client, temporal, team, field):
    r = put(client, **team)
    assert r.status_code == 422 and field in str(r.json()["detail"]) and temporal.ensured == []


def test_names_that_map_to_the_same_namespace_are_refused(client, temporal):
    assert put(client, name="Data Eng").status_code == 200
    r = put(client, name="data-eng")
    assert r.status_code == 409 and "Data Eng already uses namespace data-eng" in r.json()["detail"]


def test_a_jira_project_belongs_to_one_team(client, temporal):
    assert put(client, name="Owner", jira_projects=["OWN"]).status_code == 200
    r = put(client, name="Intruder", jira_projects=["OWN", "INT"])
    assert r.status_code == 409 and "OWN is Owner's" in r.json()["detail"] and "Intruder" not in names(client)


def test_nothing_is_saved_when_temporal_cannot_create_the_namespace(client, temporal):
    temporal.down = True
    r = put(client, name="Offline")
    assert r.status_code == 503 and "offline" in r.json()["detail"] and "Offline" not in names(client)


def test_running_tasks_block_delete_and_kills_go_to_the_team_namespace(client, temporal):
    assert put(client, name="Busy", jira_projects=["BSY"]).status_code == 200
    event(client, "task.discovered", "BSY-1")
    assert client.delete("/api/teams/Busy", headers=AUTH).status_code == 409

    assert client.post("/api/tasks/BSY-1/kill", headers=AUTH).status_code == 202  # the orchestrator signals the kill in the task's namespace
    pending = [k for k in client.get("/api/kill-requests/pending", headers=AUTH).json() if k["task_id"] == "BSY-1"]
    assert pending[0]["namespace"] == "busy"
    client.post("/api/kill-requests/handled", headers=AUTH, json={"ids": [pending[0]["id"]]})
    event(client, "task.killed", "BSY-1")
    assert client.delete("/api/teams/Busy", headers=AUTH).status_code == 200


def test_delete_removes_overrides_and_connections_but_keeps_history(client, temporal):
    assert put(client, name="Gone", jira_projects=["GON"]).status_code == 200
    assert client.put("/api/handling/teams/Gone/task", headers=AUTH, json={"fields": {"intent": True}}).status_code == 200
    event(client, "task.discovered", "GON-1")
    event(client, "task.killed", "GON-1")

    assert client.delete("/api/teams/Gone", headers=AUTH).json() == {"deleted": "Gone"}
    assert "Gone" not in names(client) and "Gone" not in client.get("/api/handling").json()["teams"]
    assert client.get("/api/tasks/GON-1").json()["task"]["team"] == "Gone"
    assert client.delete("/api/teams/Gone", headers=AUTH).status_code == 404


def test_writes_need_the_token(client):
    assert client.put("/api/teams", json={"name": "x"}).status_code == 401
    assert client.delete("/api/teams/x").status_code == 401
