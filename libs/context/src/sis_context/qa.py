"""Answered questions as searchable documents, so the next task can find the answer instead of asking again."""

from sis_context.learn import QA
from sis_context.models import Chunk, DocIn


def qa_to_doc(qa: QA, index: int, *, team: str | None, repo: str | None, jira_key: str) -> DocIn:
    text = f"Question: {qa.question}\nAnswer: {qa.answer}"
    return DocIn(kind="qa", external_id=f"{qa.task_id}:{index}", title=qa.question[:100], url="", version="1", team=team, repo=repo,
                 meta={"task_id": qa.task_id, "jira_key": jira_key, "answered_at": qa.answered_at},
                 chunks=[Chunk(text=text, heading_path="Answered question")])
