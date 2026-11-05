import json
from pathlib import Path

from sif_contracts.events import EventType, FactoryEvent

SHARED = Path(__file__).resolve().parents[2] / "libs" / "contracts" / "event_types.json"


def test_event_type_list_shared_with_typescript_is_current():
    # tests/3d-view checks the same file against the TypeScript list, so the two cannot drift apart silently.
    assert json.loads(SHARED.read_text()) == sorted(t.value for t in EventType)


def test_json_uses_camel_case_keys_like_the_typescript_clients():
    ev = FactoryEvent(type=EventType.PR_OPENED, task_id="A-1", pr_url="https://x/pull/1", cost_usd=0.5, ts=1)
    assert ev.model_dump(by_alias=True, exclude_none=True, mode="json") == {
        "type": "pr.opened", "taskId": "A-1", "prUrl": "https://x/pull/1", "costUsd": 0.5, "ts": 1}


def test_accepts_camel_case_input():
    ev = FactoryEvent.model_validate({"type": "task.discovered", "taskId": "A-1", "jiraKey": "A-1", "ts": 5})
    assert (ev.task_id, ev.jira_key) == ("A-1", "A-1")
