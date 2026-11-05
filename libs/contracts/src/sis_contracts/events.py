"""Lifecycle events emitted by the orchestrator and consumed by portal-api and the 3D view.

The JSON form uses camelCase keys (taskId, costUsd, ...) to match the TypeScript clients.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class EventType(StrEnum):
    TASK_DISCOVERED = "task.discovered"
    CLASSIFY_STARTED = "classify.started"
    CLASSIFY_RESULT = "classify.result"
    JIRA_BOUNCED = "jira.bounced"
    REPO_CHECKED_OUT = "repo.checked_out"
    CONTEXT_STARTED = "context.started"  # gathering documents, earlier answers, lessons and team knowledge for the next agent
    SANDBOX_CREATED = "sandbox.created"
    AGENT_RUNNING = "agent.running"
    AGENT_ASKED_QUESTION = "agent.asked_question"
    AGENT_ANSWERED = "agent.answered"
    PUBLISH_STARTED = "publish.started"  # git-broker is checking and pushing the change (first PR or a revision); text = the branch
    PR_OPENED = "pr.opened"
    TASK_FAILED = "task.failed"  # text = why
    WAIT_TIMED_OUT = "wait.timed_out"
    TASK_KILLED = "task.killed"
    STAGE_STARTED = "stage.started"  # text = the agent role: planner, intent, architect or developer
    CHILDREN_CREATED = "children.created"  # an epic or feature was split; text = the new issue keys
    COST_UPDATED = "cost.updated"
    LOG_LINE = "log.line"
    # After the pull request opens, the factory keeps the task until a person merges or closes it.
    REVIEW_RECEIVED = "review.received"  # review comments or failing CI on the PR; the agent is revising. text = what it was asked
    PR_UPDATED = "pr.updated"  # a revision round ended (commit pushed, or why not). text = the outcome
    PR_MERGED = "pr.merged"
    PR_CLOSED = "pr.closed"  # closed without merging
    REVIEW_STOPPED = "review.stopped"  # the factory stopped following the PR (killed, or the watch ran out); it stays open for a person. text = why


REVIEW_EVENTS = frozenset({EventType.REVIEW_RECEIVED, EventType.PR_UPDATED, EventType.PR_MERGED, EventType.PR_CLOSED, EventType.REVIEW_STOPPED})


class FactoryEvent(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    type: EventType
    task_id: str
    jira_key: str | None = None
    title: str | None = None
    team: str | None = None
    namespace: str | None = None  # Temporal namespace the workflow runs in
    repo: str | None = None
    pr_url: str | None = None
    cost_usd: float | None = None
    budget_usd: float | None = None  # current spend limit for the run
    reason: Literal["question", "budget", "approval"] | None = None  # approval: a person must approve a document or split (text has it)
    deadline: int | None = None  # epoch ms when the wait expires
    classifier: str | None = None
    result: str | None = None
    source: str | None = None
    text: str | None = None
    actor: str | None = None  # the person behind it, for the audit trail: who answered or approved, killed, asked for changes or merged
    fixups: int | None = None  # pr.merged: commits people added to the branch before merging, i.e. what the agent did not get right
    ts: int  # epoch ms
