from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Behaviour knobs shared by services. Every field is overridable as SIF_<FIELD> in .env."""

    model_config = SettingsConfigDict(env_prefix="SIF_", env_file=".env", extra="ignore")

    # Jira labels
    label_trigger: str = "sif"  # add this label to a ticket to have it picked up
    label_picked: str = "sif-picked"  # swapped in by jira-connector once the workflow has started
    label_timed_out: str = "sif-timed-out"  # set when a wait for a reply expires and the sandbox is killed

    # Talking to the agent in Jira comments
    mention: str = "@sif"  # plain-text handle; comments without it are ignored
    bot_account_id: str = ""  # Jira account id of the bot user, so real @mentions also match

    # Per-run budget (hard stop at the gateway)
    max_budget_usd: float = 2.0
    budget_extension_usd: float = 2.0  # added when a human replies "<mention> approve"

    # How long a sandbox may wait for a human reply before it is killed
    wait_timeout_minutes: int = 120

    # Lessons ("@sif remember ..."): approved team knowledge delivered to the agent
    cmd_remember: str = "remember"
    cmd_forget: str = "forget"
    lesson_auto_approve: bool = True  # role-based; False sends every lesson to the Learning page
    lesson_default_ttl_days: int = 180
    lesson_max_in_prompt: int = 8
    lesson_token_budget: int = 800
    lesson_max_chars: int = 500
    lesson_global_requires_eval: bool = False  # true: a global lesson needs a passed eval report before it can be approved
    lesson_holdout_pct: int = 20  # share of tasks that do NOT get a machine-proposed lesson, so its effect can be measured against them

    # Self-improvement cycle (portal-api): mine outcomes, measure lessons, retire what hurts, propose changes
    improve_minutes: int = 60  # how often the cycle runs; 0 = only when asked from the dashboard
    improve_min_tasks: int = 10  # finished tasks needed in each group before a lesson is judged
    improve_days: int = 30  # how far back the cycle looks

    # Replay benchmark: merged tickets re-run from their base commit, scored against what was merged
    eval_max_cases: int = 20  # most recent cases used per replay run
    eval_budget_usd: float = 1.0  # spend cap per replay (each case is replayed twice: with and without the lesson)
    eval_pass_score: float = 0.5  # similarity to the merged change that counts as solving the ticket
    eval_auto: bool = False  # true: the improvement cycle replays each lesson it proposes (costs money)

    # The daily knowledge PR: candidate lessons for a repository, bundled into one PR to its CLAUDE.md
    digest_hours: float = 24  # at most one knowledge PR per repository this often; 0 = no PRs, lessons are approved on the Learning page
    digest_file: str = "CLAUDE.md"

    # Handling policy (epic / feature / story / task)
    handling_max_chars: int = 4000  # free-text instructions per kind
    handling_max_revisions: int = 3  # feedback rounds on a document before the human must approve or stop
    jira_type_map: str = "epic:epic,story:story,task:task,bug:task,sub-task:task,subtask:task,feature:feature,new feature:feature"

    # Per-team connections (Jira, GitHub)
    allow_private_urls: bool = False  # true: connection URLs may point at private addresses (self-hosted Jira, test mocks)
    connection_cache_seconds: int = 60  # how long a service keeps a team's credentials before asking the portal again

    # Teams are Temporal namespaces
    namespace_refresh_seconds: float = 15  # how often the workers look for new team namespaces to serve
    namespace_retention_days: int = 30  # how long Temporal keeps finished workflows in a namespace created for a team

    # The model the developer agent asks for; it must be one the LLM provider behind sandbox-gateway (LLM_UPSTREAM_URL) serves.
    model_implementation: str = Field("claude-haiku-5-5", validation_alias="MODEL_IMPLEMENTATION")
