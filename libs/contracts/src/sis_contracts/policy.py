"""Pure rules shared by jira-connector, orchestrator-worker and sandbox-gateway. No I/O, so they are easy to test."""

import re
from enum import StrEnum

from sis_contracts.settings import Settings


class Decision(StrEnum):
    APPROVE = "approve"
    STOP = "stop"
    ANSWER = "answer"


def should_pick(labels: list[str], s: Settings) -> bool:
    """A ticket is picked up when it has the trigger label and has not been picked already."""
    return s.label_trigger in labels and s.label_picked not in labels


def is_for_agent(comment: str, s: Settings) -> bool:
    """True if the comment addresses the agent, via the plain-text handle or a real Jira mention of the bot."""
    handle = re.compile(rf"(?<![\w@]){re.escape(s.mention)}(?!\w)", re.IGNORECASE)
    if handle.search(comment):
        return True
    return bool(s.bot_account_id) and f"[~accountid:{s.bot_account_id}]" in comment


def message_for_agent(comment: str, s: Settings) -> str:
    """The comment text with the mention removed."""
    text = re.sub(rf"(?<![\w@]){re.escape(s.mention)}(?!\w)", "", comment, flags=re.IGNORECASE)
    if s.bot_account_id:
        text = text.replace(f"[~accountid:{s.bot_account_id}]", "")
    return " ".join(text.split())


def classify_reply(message: str) -> Decision:
    """After a budget request, 'approve' grants an extension and 'stop' ends the run; anything else is an answer."""
    first = message.strip().lower().split(maxsplit=1)[0] if message.strip() else ""
    if first in {"approve", "continue"}:
        return Decision.APPROVE
    if first in {"stop", "cancel"}:
        return Decision.STOP
    return Decision.ANSWER
