"""Workflows API: the timeline built from Temporal history, the filters written into visibility queries, and the read-only endpoints against a
fake Temporal. The endpoint tests need DATABASE_URL (teams decide which namespaces may be read), like test_api.py."""

import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from google.protobuf.duration_pb2 import Duration
from google.protobuf.timestamp_pb2 import Timestamp
from temporalio.api.enums.v1 import EventType, PendingActivityState
from temporalio.api.failure.v1 import Failure
from temporalio.api.history.v1 import HistoryEvent
from temporalio.api.workflow.v1 import PendingActivityInfo
from temporalio.api.workflowservice.v1 import DescribeWorkflowExecutionResponse
from temporalio.client import WorkflowExecutionStatus

from portal_api.workflows import pending, timeline, visibility_query

AUTH = {"Authorization": "Bearer test-token"}
needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL")


def event(n: int, kind: str, **attributes) -> HistoryEvent:
    return HistoryEvent(event_id=n, event_type=EventType.Value(f"EVENT_TYPE_{kind}"), event_time=Timestamp(seconds=1_700_000_000 + n), **attributes)


HISTORY = [
    event(1, "WORKFLOW_EXECUTION_STARTED", workflow_execution_started_event_attributes=dict(workflow_type={"name": "TaskWorkflow"}, task_queue={"name": "sis-orchestrator"})),
    event(2, "WORKFLOW_TASK_SCHEDULED"), event(3, "WORKFLOW_TASK_STARTED"), event(4, "WORKFLOW_TASK_COMPLETED"),
    event(5, "ACTIVITY_TASK_SCHEDULED", activity_task_scheduled_event_attributes=dict(activity_type={"name": "checkout"})),
    event(6, "ACTIVITY_TASK_STARTED", activity_task_started_event_attributes=dict(scheduled_event_id=5, attempt=2)),
    event(7, "ACTIVITY_TASK_COMPLETED", activity_task_completed_event_attributes=dict(scheduled_event_id=5)),
    event(8, "TIMER_STARTED", timer_started_event_attributes=dict(timer_id="t1", start_to_fire_timeout=Duration(seconds=3 * 86400))),
    event(9, "WORKFLOW_EXECUTION_SIGNALED", workflow_execution_signaled_event_attributes=dict(signal_name="answer")),
    event(10, "TIMER_CANCELED", timer_canceled_event_attributes=dict(timer_id="t1")),
    event(11, "ACTIVITY_TASK_SCHEDULED", activity_task_scheduled_event_attributes=dict(activity_type={"name": "run_agent"})),
    event(12, "ACTIVITY_TASK_FAILED", activity_task_failed_event_attributes=dict(scheduled_event_id=11,
          failure=Failure(message="activity error", cause=Failure(message="the agent exited with an error")))),
    event(13, "WORKFLOW_EXECUTION_FAILED", workflow_execution_failed_event_attributes=dict(failure=Failure(message="gave up"))),
]


def test_timeline_folds_each_activity_and_timer_into_one_step_and_skips_bookkeeping():
    steps = timeline(HISTORY)
    assert [(s["kind"], s["name"], s["status"]) for s in steps] == [
        ("workflow", "Workflow started", "completed"), ("activity", "checkout", "completed"), ("timer", "Wait 3d", "canceled"),
        ("signal", "Signal answer", "completed"), ("activity", "run_agent", "failed"), ("workflow", "Workflow failed", "failed")]
    checkout, wait, agent = steps[1], steps[2], steps[4]
    assert checkout["attempt"] == 2 and checkout["at"].startswith("2023-11-14") and checkout["ended"]
    assert wait["detail"].startswith("ended early")
    assert agent["detail"] == "activity error ← the agent exited with an error"  # the cause is where the real reason is
    assert steps[0]["detail"] == "TaskWorkflow on queue sis-orchestrator"


def test_timeline_leaves_out_progress_reports_unless_they_failed():
    report = [event(1, "ACTIVITY_TASK_SCHEDULED", activity_task_scheduled_event_attributes=dict(activity_type={"name": "emit_event"})),
              event(2, "ACTIVITY_TASK_COMPLETED", activity_task_completed_event_attributes=dict(scheduled_event_id=1)),
              event(3, "ACTIVITY_TASK_SCHEDULED", activity_task_scheduled_event_attributes=dict(activity_type={"name": "emit_event"})),
              event(4, "ACTIVITY_TASK_FAILED", activity_task_failed_event_attributes=dict(scheduled_event_id=3, failure=Failure(message="portal-api down")))]
    assert [(s["id"], s["status"]) for s in timeline(report)] == [(3, "failed")]


def test_timeline_cuts_long_failure_messages():
    e = event(1, "WORKFLOW_EXECUTION_FAILED", workflow_execution_failed_event_attributes=dict(failure=Failure(message="x" * 5000)))
    assert len(timeline([e])[0]["detail"]) == 2001


def test_pending_activities_say_how_far_retries_got():
    raw = DescribeWorkflowExecutionResponse(pending_activities=[PendingActivityInfo(
        activity_type={"name": "run_agent"}, state=PendingActivityState.PENDING_ACTIVITY_STATE_STARTED, attempt=3, maximum_attempts=5,
        last_failure=Failure(message="sandbox did not start"))])
    [a] = pending(raw)
    assert (a["name"], a["state"], a["attempt"], a["max_attempts"], a["last_failure"], a["last_heartbeat"]) == ("run_agent", "started", 3, 5, "sandbox did not start", None)


def test_filters_become_a_visibility_query_and_anything_else_is_refused():
    assert visibility_query(None, None) == ""
    assert visibility_query("running", "SCRUM-16") == 'ExecutionStatus = "Running" AND (WorkflowId STARTS_WITH "SCRUM-16" OR WorkflowId STARTS_WITH "task-SCRUM-16")'
    for status, q in [("sleeping", None), (None, 'x" OR WorkflowId != "'), (None, "a b")]:
        with pytest.raises(HTTPException) as e:
            visibility_query(status, q)
        assert e.value.status_code == 422


# ---- endpoints, against a fake Temporal

class Listing:
    def __init__(self, rows, token):
        self.current_page, self.next_page_token, self._rows = None, token, rows

    async def fetch_next_page(self):
        self.current_page = self._rows


class Handle:
    def __init__(self, temporal, workflow_id):
        self.temporal, self.workflow_id = temporal, workflow_id

    async def describe(self):
        if self.workflow_id == "task-NOPE-1":
            raise RuntimeError("workflow not found for ID: task-NOPE-1")
        return SimpleNamespace(**vars(execution(self.workflow_id)), parent_id=None, raw_description=DescribeWorkflowExecutionResponse())

    async def fetch_history_events(self):
        for e in HISTORY:
            yield e

    async def query(self, name):
        self.temporal.queries.append(name)
        return ["2026-11-01T10:00:00Z sandbox  cloned the repo"]


def execution(workflow_id, status=WorkflowExecutionStatus.FAILED):
    return SimpleNamespace(id=workflow_id, run_id="r1", workflow_type="TaskWorkflow", status=status, task_queue="sis-orchestrator", history_length=13,
                           start_time=datetime(2026, 11, 1, 10, tzinfo=UTC), close_time=datetime(2026, 11, 1, 10, 5, tzinfo=UTC))


class FakeTemporal:
    def __init__(self):
        self.listed, self.queries = [], []

    async def client(self, namespace):
        return self

    def list_workflows(self, query, page_size, next_page_token):
        self.listed.append((query, next_page_token))
        return Listing([execution("task-SCRUM-16", WorkflowExecutionStatus.RUNNING)], b"\x01\x02")

    def get_workflow_handle(self, workflow_id, run_id=None):
        return Handle(self, workflow_id)


@pytest.fixture(scope="module")
def client():
    os.environ["SIS_INGEST_TOKEN"] = "test-token"
    from portal_api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def temporal(client):
    t = FakeTemporal()
    client.app.state.namespaces = t
    yield t
    del client.app.state.namespaces


@needs_db
def test_list_passes_filters_and_paging_through(client, temporal):
    r = client.get("/api/workflows", params={"status": "running", "q": "SCRUM", "page": "0a0b"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["next"] == "0102" and body["workflows"][0] | {"started": None} == {
        "id": "task-SCRUM-16", "run_id": "r1", "type": "TaskWorkflow", "status": "running", "started": None,
        "closed": "2026-11-01T10:05:00+00:00", "task_queue": "sis-orchestrator", "events": 13}
    assert temporal.listed == [('ExecutionStatus = "Running" AND (WorkflowId STARTS_WITH "SCRUM" OR WorkflowId STARTS_WITH "task-SCRUM")', b"\x0a\x0b")]


@needs_db
def test_only_known_namespaces_and_well_formed_input_reach_temporal(client, temporal):
    assert client.get("/api/workflows", params={"namespace": "someone-else"}).status_code == 404
    assert client.get("/api/workflows", params={"page": "not-a-token"}).status_code == 422
    assert client.get("/api/workflows/default/task-SCRUM-16", params={"run_id": "r 1"}).status_code == 422
    assert temporal.listed == []


@needs_db
def test_detail_has_the_timeline_and_the_task_log(client, temporal):
    r = client.get("/api/workflows/default/task-SCRUM-16")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "failed" and body["steps"][4]["name"] == "run_agent" and body["cut"] is False
    assert body["logs"] == ["2026-11-01T10:00:00Z sandbox  cloned the repo"] and temporal.queries == ["logs"]
    assert client.get("/api/workflows/default/task-NOPE-1").status_code == 404


@needs_db
def test_there_is_no_way_to_change_a_workflow(client, temporal):
    for method in ("post", "put", "delete"):
        assert getattr(client, method)("/api/workflows/default/task-SCRUM-16").status_code == 405


@needs_db
def test_without_temporal_the_page_says_so(client):
    client.app.state.namespaces = None
    try:
        assert client.get("/api/workflows").status_code == 503
    finally:
        del client.app.state.namespaces
