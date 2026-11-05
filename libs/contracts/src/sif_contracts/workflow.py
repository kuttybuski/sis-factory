"""Types and names shared by jira-connector, orchestrator-worker and sandbox-gateway. Plain dataclasses, so Temporal can carry them."""

from dataclasses import dataclass, field

ORCHESTRATOR_QUEUE = "sif-orchestrator"
JIRA_QUEUE = "sif-jira"
WORKFLOW = "TaskWorkflow"
# Once its PR is open a task continues as this type, under the same workflow id: it no longer counts against the concurrency limit.
REVIEW_WORKFLOW = "ReviewWorkflow"
EVAL_WORKFLOW = "EvalWorkflow"  # replays past tickets with and without a lesson
REPLAY_WORKFLOW = "ReplayWorkflow"  # one replay: an agent on a past ticket's base commit, no PR, no Jira

# Signals
SIG_QUESTION, SIG_REPLY, SIG_DONE, SIG_FAILED, SIG_KILL, SIG_COST, SIG_LOG = "agent_question", "human_reply", "agent_done", "agent_failed", "kill", "cost", "log"
# Queries
Q_ANSWER, Q_STATE, Q_LOGS = "get_answer", "state", "logs"  # logs: the task's whole log, shown on the dashboard's workflow page


def workflow_id(jira_key: str) -> str:
    return f"task-{jira_key}"


@dataclass
class TaskInput:
    jira_key: str
    title: str
    description: str = ""
    reporter: str = ""
    team: str | None = None
    namespace: str = "default"
    repo: str = ""
    base_branch: str = "main"
    labels: list[str] = field(default_factory=list)
    budget_usd: float = 2.0
    budget_extension_usd: float = 2.0
    wait_timeout_seconds: int = 7200
    max_run_seconds: int = 14400
    mention: str = "@sif"
    kind: str = "task"  # epic | feature | story | task, from the Jira issue type
    max_revisions: int = 3  # feedback rounds on a document before a person must approve or stop
    parent_key: str = ""  # the epic or feature this issue was split from, if any
    policy: dict = field(default_factory=dict)  # sif_contracts.handling.Policy as resolved at pickup; empty means the built-in default for the kind
    review_poll_seconds: int = 0  # how often an open PR is checked for review comments, failing CI and merge; 0 ends the task when the PR opens
    review_watch_seconds: int = 14 * 24 * 3600  # stop watching a PR nobody acts on after this long
    max_review_rounds: int = 3  # agent revisions per PR before the reviewer must finish it by hand


@dataclass
class ReviewInput:
    """What ReviewWorkflow needs to carry on from TaskWorkflow once the PR is open."""
    task: TaskInput
    pr_number: int
    pr_url: str
    branch: str
    budget_usd: float
    spent_usd: float
    docs: dict[str, str] = field(default_factory=dict)
    profile: str = ""
    risk: str = ""
    base_commit: str = ""  # what the first attempt started from; with the merged head it becomes a replay case


@dataclass
class EvalCase:
    """A merged ticket kept for replay: where the work started, what was asked, and what people finally merged (the reference diff)."""
    task_id: str
    repo: str
    base_commit: str
    title: str
    description: str
    reference: str
    team: str | None = None


@dataclass
class ReplayInput:
    task: TaskInput  # jira_key is the replay's own key (eval<run>-<case>-<arm>), so its sandbox signals reach this workflow
    base_commit: str
    lesson_id: int
    with_lesson: bool


@dataclass
class EvalInput:
    run_id: int
    lesson_id: int
    cases: list[EvalCase]
    budget_usd: float = 1.0  # per replay
    pass_score: float = 0.5  # a replay at or above this similarity to the merged change counts as a pass
    max_run_seconds: int = 1800


@dataclass
class Feedback:
    """One thing a reviewer or CI asked for on the PR."""
    id: str
    author: str
    body: str
    kind: str = "comment"  # review | comment | ci
    path: str = ""


@dataclass
class Question:
    id: str
    text: str
    reason: str = "question"  # or "budget"


@dataclass
class Reply:
    text: str
    author: str = ""
    comment_id: str = ""


@dataclass
class AgentResult:
    patch: str
    summary: str = ""
    title: str = ""
    body: str = ""
    documents: dict[str, str] = field(default_factory=dict)  # file name -> text, from the intent, architect and planner roles


@dataclass
class Answer:
    status: str  # pending | answered | timed_out | stopped
    text: str = ""
    approved_budget_usd: float | None = None
