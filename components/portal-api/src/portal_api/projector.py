"""Pure projection of lifecycle events onto a task row. No I/O, so it is easy to test."""

from datetime import UTC, datetime
from typing import Any

from sif_contracts.events import REVIEW_EVENTS, EventType, FactoryEvent

# pr_opened means "in review": the factory still follows the PR, but only review events move the task on from there (to merged or closed,
# or back through a revision round). A revision that fails leaves the task failed until its pr.updated puts it back in review.
# left_open: the factory stopped following the PR (killed, or no decision in time); a person finishes it.
TERMINAL = frozenset({"pr_opened", "split", "bounced", "timed_out", "failed", "killed", "merged", "closed", "left_open"})
FINAL = frozenset({"merged", "closed", "left_open"})

STAGE_BY_EVENT: dict[EventType, str] = {
    EventType.TASK_DISCOVERED: "discovered",
    EventType.CLASSIFY_STARTED: "classifying",
    EventType.JIRA_BOUNCED: "bounced",
    EventType.REPO_CHECKED_OUT: "checked_out",
    EventType.CONTEXT_STARTED: "context",
    EventType.PUBLISH_STARTED: "publishing",
    EventType.SANDBOX_CREATED: "sandbox_created",
    EventType.AGENT_RUNNING: "running",
    EventType.AGENT_ASKED_QUESTION: "asking",
    EventType.AGENT_ANSWERED: "running",
    EventType.PR_OPENED: "pr_opened",
    EventType.CHILDREN_CREATED: "split",
    EventType.TASK_FAILED: "failed",
    EventType.WAIT_TIMED_OUT: "timed_out",
    EventType.TASK_KILLED: "killed",
    EventType.REVIEW_RECEIVED: "revising",
    EventType.PR_UPDATED: "pr_opened",
    EventType.PR_MERGED: "merged",
    EventType.PR_CLOSED: "closed",
    EventType.REVIEW_STOPPED: "left_open",
}


def to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


def accepts(stage: str, has_pr: bool, ev_type: EventType) -> bool:
    """Whether a task in this stage still takes this event."""
    if stage not in TERMINAL:
        return True
    return has_pr and stage not in FINAL and ev_type in REVIEW_EVENTS


def project(task: dict[str, Any] | None, ev: FactoryEvent, defaults: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Returns the updated task row, or None if the event changes nothing (unknown task, or a late event for a finished one)."""
    if task is None:
        if ev.type is not EventType.TASK_DISCOVERED:
            return None
        defaults = defaults or {}
        row: dict[str, Any] = {
            "id": ev.task_id,
            "jira_key": ev.jira_key or ev.task_id,
            "title": ev.title or "",
            "team": ev.team or defaults.get("team"),
            "namespace": ev.namespace or defaults.get("namespace"),
            "repo": ev.repo,
            "stage": "discovered",
            "outcome": None,
            "profile": None,
            "cost_usd": 0.0,
            "budget_usd": ev.budget_usd,
            "pr_url": None,
            "started_at": to_dt(ev.ts),
            "ended_at": None,
            "pr_opened_at": None,
            "review_rounds": 0,
            "merged_at": None,
            "human_fixups": None,
        }
    elif not accepts(task["stage"], bool(task.get("pr_url")), ev.type):
        return None
    else:
        row = dict(task)

    stage = STAGE_BY_EVENT.get(ev.type)
    if stage:
        row["stage"] = stage
    if ev.cost_usd is not None:
        row["cost_usd"] = ev.cost_usd
    if ev.budget_usd is not None:
        row["budget_usd"] = ev.budget_usd
    if ev.type is EventType.CLASSIFY_RESULT and ev.classifier == "routing":
        row["profile"] = ev.result
    if ev.pr_url:
        row["pr_url"] = ev.pr_url
    if ev.repo and not row["repo"]:
        row["repo"] = ev.repo
    if ev.type is EventType.PR_OPENED:
        row["pr_opened_at"] = to_dt(ev.ts)
    if ev.type is EventType.REVIEW_RECEIVED:
        row["review_rounds"] = (row.get("review_rounds") or 0) + 1
    if ev.type is EventType.PR_MERGED:
        row["merged_at"] = to_dt(ev.ts)
        row["human_fixups"] = ev.fixups
    if row["stage"] in TERMINAL:
        row["outcome"] = row["stage"]
        row["ended_at"] = to_dt(ev.ts)
    else:
        row["outcome"], row["ended_at"] = None, None  # a revision round makes the task active again
    return row
