"""Workflows, read from Temporal for the dashboard's own Workflows pages (it used to frame the Temporal UI). Read-only on purpose: stopping a
task goes through the dashboard's Kill, which also stops the sandbox and records who did it."""

import asyncio
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from sif_contracts.workflow import Q_LOGS, WORKFLOW

from portal_api import db
from portal_api.auth import pool
from portal_api.teams import namespaces

router = APIRouter()

PAGE = 50
MAX_EVENTS = 5000  # a task with many review rounds stays well under this; past it the timeline says it was cut
DETAIL_CHARS = 2000
# Filter values are written into a Temporal visibility query, so only fixed statuses and plain workflow ids get there.
STATUS = {"running": "Running", "completed": "Completed", "failed": "Failed", "terminated": "Terminated", "canceled": "Canceled",
          "timed_out": "TimedOut", "continued": "ContinuedAsNew"}
WORKFLOW_ID = re.compile(r"[A-Za-z0-9_.:@-]{1,200}")
PAGE_TOKEN = re.compile(r"(?:[0-9a-f]{2}){1,4096}")
# Bookkeeping that says nothing to a person reading the timeline (workflow tasks are Temporal handing work to a worker).
QUIET = {"WORKFLOW_TASK_SCHEDULED", "WORKFLOW_TASK_STARTED", "WORKFLOW_TASK_COMPLETED", "MARKER_RECORDED", "UPSERT_WORKFLOW_SEARCH_ATTRIBUTES"}
# Activities that only report progress to the dashboard; shown when they fail, since then the dashboard missed something.
REPORTING = {"emit_event"}


def visibility_query(status: str | None, q: str | None) -> str:
    parts = []
    if status:
        if status not in STATUS:
            raise HTTPException(422, f"status must be one of {', '.join(STATUS)}")
        parts.append(f'ExecutionStatus = "{STATUS[status]}"')
    if q:
        q = q.strip()
        if not WORKFLOW_ID.fullmatch(q):
            raise HTTPException(422, "search by workflow id or ticket key: letters, digits and - _ . : @ only")
        parts.append(f'(WorkflowId STARTS_WITH "{q}" OR WorkflowId STARTS_WITH "task-{q}")')  # a ticket key finds its task workflow
    return " AND ".join(parts)


async def client_for(request: Request, p, namespace: str) -> Any:
    """A Temporal client for the default namespace or a team's; any other name is refused rather than passed on."""
    temporal = namespaces(request)
    if temporal is None:
        raise HTTPException(503, "Temporal is not configured (TEMPORAL_ADDRESS)")
    if namespace != "default" and namespace not in {t["temporal_namespace"] for t in await db.list_teams(p)}:
        raise HTTPException(404, f"no team runs in namespace {namespace}")
    return await temporal.client(namespace)


def iso(value: Any) -> str | None:
    """A datetime, or a protobuf Timestamp, as ISO 8601; None when unset."""
    if value is None:
        return None
    if hasattr(value, "ToDatetime"):
        return value.ToDatetime().isoformat() + "Z" if value.seconds or value.nanos else None
    return value.isoformat()


def status_name(status: Any) -> str:
    return status.name.lower() if status is not None else "unknown"


def row(w: Any) -> dict[str, Any]:
    return {"id": w.id, "run_id": w.run_id, "type": w.workflow_type, "status": status_name(w.status), "started": iso(w.start_time),
            "closed": iso(w.close_time), "task_queue": w.task_queue, "events": w.history_length}


def short(text: str) -> str:
    return text if len(text) <= DETAIL_CHARS else text[:DETAIL_CHARS] + "…"


def failure(f: Any) -> str | None:
    """The message of a failure and of what caused it, which is usually the useful part (an activity's own error under a retry error)."""
    messages = []
    while f is not None and f.message and len(messages) < 4:
        messages.append(f.message)
        f = f.cause if f.HasField("cause") else None
    return short(" ← ".join(messages)) if messages else None


def seconds(d: Any) -> str:
    s = int(d.ToTimedelta().total_seconds())
    return f"{s // 86400}d" if s >= 86400 and s % 86400 == 0 else f"{s // 3600}h {s % 3600 // 60}m" if s >= 3600 else f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


def timeline(events: list[Any]) -> list[dict[str, Any]]:
    """History events as steps a person can follow: an activity's scheduled, started and finished events become one step, likewise timers and
    child workflows; workflow-task bookkeeping is left out. Inputs and results are not shown: they hold ticket text and agent output, which the
    task's own page already shows with its context."""
    from temporalio.api.enums.v1 import EventType

    steps: list[dict[str, Any]] = []
    by_event: dict[int, dict[str, Any]] = {}
    by_timer: dict[str, dict[str, Any]] = {}

    def add(e: Any, kind: str, name: str, status: str, detail: str | None = None) -> dict[str, Any]:
        step = {"id": e.event_id, "at": iso(e.event_time), "kind": kind, "name": name, "status": status, "detail": detail, "ended": None, "attempt": None}
        steps.append(step)
        return step

    def finish(step: dict[str, Any] | None, e: Any, status: str, detail: str | None = None) -> None:
        if step is not None:
            step.update(status=status, ended=iso(e.event_time), detail=detail or step["detail"])

    for e in events:
        t = EventType.Name(e.event_type).removeprefix("EVENT_TYPE_")
        if t in QUIET:
            continue
        if t == "WORKFLOW_EXECUTION_STARTED":
            a = e.workflow_execution_started_event_attributes
            add(e, "workflow", "Workflow started", "completed", f"{a.workflow_type.name} on queue {a.task_queue.name}")
        elif t == "ACTIVITY_TASK_SCHEDULED":
            by_event[e.event_id] = add(e, "activity", e.activity_task_scheduled_event_attributes.activity_type.name, "scheduled")
        elif t == "ACTIVITY_TASK_STARTED":
            a = e.activity_task_started_event_attributes
            if step := by_event.get(a.scheduled_event_id):
                step.update(status="running", attempt=a.attempt)
                if a.HasField("last_failure"):
                    step["detail"] = f"earlier attempt failed: {failure(a.last_failure)}"
        elif t == "ACTIVITY_TASK_COMPLETED":
            finish(by_event.get(e.activity_task_completed_event_attributes.scheduled_event_id), e, "completed")
        elif t == "ACTIVITY_TASK_FAILED":
            a = e.activity_task_failed_event_attributes
            finish(by_event.get(a.scheduled_event_id), e, "failed", failure(a.failure))
        elif t == "ACTIVITY_TASK_TIMED_OUT":
            a = e.activity_task_timed_out_event_attributes
            finish(by_event.get(a.scheduled_event_id), e, "timed_out", failure(a.failure))
        elif t == "ACTIVITY_TASK_CANCELED":
            finish(by_event.get(e.activity_task_canceled_event_attributes.scheduled_event_id), e, "canceled")
        elif t == "TIMER_STARTED":
            a = e.timer_started_event_attributes
            by_timer[a.timer_id] = add(e, "timer", f"Wait {seconds(a.start_to_fire_timeout)}", "running")
        elif t == "TIMER_FIRED":
            finish(by_timer.get(e.timer_fired_event_attributes.timer_id), e, "completed")
        elif t == "TIMER_CANCELED":
            finish(by_timer.get(e.timer_canceled_event_attributes.timer_id), e, "canceled", "ended early: what it waited for arrived")
        elif t == "WORKFLOW_EXECUTION_SIGNALED":
            add(e, "signal", f"Signal {e.workflow_execution_signaled_event_attributes.signal_name}", "completed")
        elif t == "START_CHILD_WORKFLOW_EXECUTION_INITIATED":
            a = e.start_child_workflow_execution_initiated_event_attributes
            by_event[e.event_id] = add(e, "child", f"Child {a.workflow_type.name}", "scheduled", a.workflow_id)
        elif t.startswith("CHILD_WORKFLOW_EXECUTION_") or t == "START_CHILD_WORKFLOW_EXECUTION_FAILED":
            a = getattr(e, e.WhichOneof("attributes"))
            outcome = t.removeprefix("CHILD_WORKFLOW_EXECUTION_").removeprefix("START_CHILD_WORKFLOW_EXECUTION_").lower()
            step = by_event.get(a.initiated_event_id)
            if outcome == "started":
                if step:
                    step["status"] = "running"
            else:
                has_failure = "failure" in a.DESCRIPTOR.fields_by_name and a.HasField("failure")
                finish(step, e, outcome, failure(a.failure) if has_failure else None)
        elif t == "WORKFLOW_EXECUTION_COMPLETED":
            add(e, "workflow", "Workflow completed", "completed")
        elif t == "WORKFLOW_EXECUTION_FAILED":
            add(e, "workflow", "Workflow failed", "failed", failure(e.workflow_execution_failed_event_attributes.failure))
        elif t == "WORKFLOW_EXECUTION_TIMED_OUT":
            add(e, "workflow", "Workflow timed out", "timed_out")
        elif t == "WORKFLOW_EXECUTION_TERMINATED":
            a = e.workflow_execution_terminated_event_attributes
            add(e, "workflow", "Workflow terminated", "terminated", short(" · ".join(x for x in (a.reason, a.identity) if x)) or None)
        elif t == "WORKFLOW_EXECUTION_CANCELED":
            add(e, "workflow", "Workflow canceled", "canceled")
        elif t == "WORKFLOW_EXECUTION_CONTINUED_AS_NEW":
            add(e, "workflow", "Continued as a new run", "completed", e.workflow_execution_continued_as_new_event_attributes.new_execution_run_id)
        elif t == "WORKFLOW_TASK_FAILED":
            add(e, "workflow", "Worker could not run the workflow code", "failed", failure(e.workflow_task_failed_event_attributes.failure))
        else:
            add(e, "other", t.replace("_", " ").capitalize(), "completed")
    return [s for s in steps if not (s["kind"] == "activity" and s["name"] in REPORTING and s["status"] == "completed")]


def pending(raw: Any) -> list[dict[str, Any]]:
    from temporalio.api.enums.v1 import PendingActivityState

    return [{"name": a.activity_type.name, "state": PendingActivityState.Name(a.state).removeprefix("PENDING_ACTIVITY_STATE_").lower(),
             "attempt": a.attempt, "max_attempts": a.maximum_attempts or None, "scheduled": iso(a.scheduled_time),
             "last_started": iso(a.last_started_time), "last_heartbeat": iso(a.last_heartbeat_time),
             "next_attempt": iso(a.next_attempt_schedule_time), "last_failure": failure(a.last_failure) if a.HasField("last_failure") else None}
            for a in raw.pending_activities]


@router.get("/api/workflows")
async def list_workflows(request: Request, namespace: str = "default", status: str | None = None, q: str | None = None,
                         page: str | None = Query(None, max_length=8192), p=Depends(pool)) -> dict[str, Any]:
    query = visibility_query(status, q)
    if page and not PAGE_TOKEN.fullmatch(page):
        raise HTTPException(422, "page must be the token from the previous response")
    client = await client_for(request, p, namespace)
    try:
        listing = client.list_workflows(query or None, page_size=PAGE, next_page_token=bytes.fromhex(page) if page else None)
        await listing.fetch_next_page()
    except Exception as e:  # noqa: BLE001 - the page says Temporal could not be read instead of failing
        raise HTTPException(503, f"Temporal could not list workflows ({type(e).__name__}: {e})") from e
    token = listing.next_page_token
    return {"namespace": namespace, "workflows": [row(w) for w in listing.current_page or []], "next": token.hex() if token else None}


@router.get("/api/workflows/{namespace}/{workflow_id}")
async def workflow(request: Request, namespace: str, workflow_id: str, run_id: str | None = None, p=Depends(pool)) -> dict[str, Any]:
    if not WORKFLOW_ID.fullmatch(workflow_id) or (run_id and not WORKFLOW_ID.fullmatch(run_id)):
        raise HTTPException(422, "not a workflow id")
    client = await client_for(request, p, namespace)
    handle = client.get_workflow_handle(workflow_id, run_id=run_id)
    try:
        desc = await handle.describe()
    except Exception as e:  # noqa: BLE001
        if "not found" in str(e).lower():
            raise HTTPException(404, f"no workflow {workflow_id} in {namespace}") from e
        raise HTTPException(503, f"Temporal could not describe the workflow ({type(e).__name__}: {e})") from e
    events, cut = [], False
    async for e in handle.fetch_history_events():
        if len(events) == MAX_EVENTS:
            cut = True
            break
        events.append(e)
    logs, logs_error = None, None
    if desc.workflow_type == WORKFLOW:
        try:  # a query replays the workflow on a worker, so it needs one running; a finished task still answers
            logs = await asyncio.wait_for(handle.query(Q_LOGS), timeout=5)
        except Exception as e:  # noqa: BLE001
            logs_error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    return {**row(desc), "namespace": namespace, "parent_id": desc.parent_id, "pending": pending(desc.raw_description),
            "steps": timeline(events), "cut": cut, "logs": logs, "logs_error": logs_error}
