"""The only place Jira writes happen for the workflow. Runs on its own task queue so the orchestrator never sees Jira credentials."""

from temporalio import activity
from temporalio.exceptions import ApplicationError

import httpx

from sif_contracts import Settings
from sif_contracts import handling as H

from jira_connector.client import JiraClient
from jira_connector.router import NoConnection

_jira: JiraClient | None = None


def set_client(c: JiraClient) -> None:
    global _jira
    _jira = c


def _guard(e: httpx.HTTPStatusError) -> None:
    if 400 <= e.response.status_code < 500 and e.response.status_code not in (408, 429):
        raise ApplicationError(f"Jira refused the request ({e.response.status_code})", non_retryable=True) from e
    raise e


@activity.defn(name="jira_comment")
async def jira_comment(key: str, text: str) -> None:
    try:
        await _jira.add_comment(key, text)
    except httpx.HTTPStatusError as e:
        _guard(e)
    except NoConnection as e:
        raise ApplicationError(str(e), non_retryable=True) from e


@activity.defn(name="jira_set_labels")
async def jira_set_labels(key: str, add: list[str] | None, remove: list[str] | None) -> None:
    try:
        await _jira.update_labels(key, add or [], remove or [])
    except httpx.HTTPStatusError as e:
        _guard(e)
    except NoConnection as e:
        raise ApplicationError(str(e), non_retryable=True) from e


@activity.defn(name="jira_create_issue")
async def jira_create_issue(project: str, summary: str, description: str, kind: str, parent_key: str) -> str:
    """Creates a child issue of the given kind (story or feature) carrying the trigger label, so the next pickup cycle starts it like any other ticket."""
    s = Settings()
    try:
        return await _jira.create_issue(project, summary, description, H.jira_type_for(kind, s.jira_type_map), parent_key or None, [s.label_trigger])
    except httpx.HTTPStatusError as e:
        _guard(e)
    except NoConnection as e:
        raise ApplicationError(str(e), non_retryable=True) from e


ALL = [jira_comment, jira_set_labels, jira_create_issue]
