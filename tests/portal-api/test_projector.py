from sif_contracts.events import EventType as T
from sif_contracts.events import FactoryEvent

from portal_api.projector import project


def ev(kind: T, **kw) -> FactoryEvent:
    return FactoryEvent(type=kind, task_id="PAY-1", ts=kw.pop("ts", 1000), **kw)


def run(*events: FactoryEvent, defaults=None):
    row = None
    for e in events:
        row = project(row, e, defaults) or row
    return row


def test_unknown_task_is_ignored():
    assert project(None, ev(T.AGENT_RUNNING)) is None


def test_discovery_creates_row_with_team_defaults():
    row = run(ev(T.TASK_DISCOVERED, jira_key="PAY-1", title="Fix"), defaults={"team": "Payments", "namespace": "payments"})
    assert (row["team"], row["namespace"], row["stage"], row["cost_usd"]) == ("Payments", "payments", "discovered", 0.0)


def test_explicit_team_beats_defaults():
    row = run(ev(T.TASK_DISCOVERED, team="Mobile"), defaults={"team": "Payments"})
    assert row["team"] == "Mobile"


def test_splitting_an_epic_finishes_it_with_the_outcome_split():
    row = run(ev(T.TASK_DISCOVERED), ev(T.STAGE_STARTED, text="planner"), ev(T.CHILDREN_CREATED, text="PAY-101, PAY-102", ts=7000), ev(T.AGENT_RUNNING, ts=8000))
    assert (row["stage"], row["outcome"]) == ("split", "split") and row["ended_at"].timestamp() == 7.0


def test_a_stage_marker_does_not_change_where_the_task_is():
    row = run(ev(T.TASK_DISCOVERED), ev(T.AGENT_RUNNING), ev(T.STAGE_STARTED, text="architect"))
    assert row["stage"] == "running"


def test_cost_profile_and_pr_are_recorded_and_finish_the_task():
    row = run(
        ev(T.TASK_DISCOVERED), ev(T.CLASSIFY_RESULT, classifier="routing", result="cheap-best"),
        ev(T.COST_UPDATED, cost_usd=1.25), ev(T.PR_OPENED, pr_url="https://x/pull/9", ts=5000))
    assert row["profile"] == "cheap-best"
    assert row["cost_usd"] == 1.25
    assert (row["stage"], row["outcome"], row["pr_url"]) == ("pr_opened", "pr_opened", "https://x/pull/9")
    assert row["ended_at"].timestamp() == 5.0


def test_only_the_routing_classifier_sets_the_profile():
    row = run(ev(T.TASK_DISCOVERED), ev(T.CLASSIFY_RESULT, classifier="risk-tier", result="low"))
    assert row["profile"] is None


def test_late_events_after_a_terminal_state_are_ignored():
    row = run(ev(T.TASK_DISCOVERED), ev(T.TASK_KILLED))
    assert project(row, ev(T.AGENT_RUNNING)) is None


def test_budget_raise_on_approval():
    row = run(ev(T.TASK_DISCOVERED, budget_usd=2.0), ev(T.AGENT_ANSWERED, budget_usd=4.0))
    assert row["budget_usd"] == 4.0


def test_timeout_is_a_terminal_outcome():
    row = run(ev(T.TASK_DISCOVERED), ev(T.AGENT_ASKED_QUESTION, reason="budget"), ev(T.WAIT_TIMED_OUT))
    assert row["outcome"] == "timed_out"


def test_a_task_follows_its_pr_through_review_rounds_to_merge():
    opened = run(ev(T.TASK_DISCOVERED), ev(T.PR_OPENED, pr_url="https://x/pull/1", ts=2000))
    assert (opened["outcome"], opened["pr_opened_at"] is not None) == ("pr_opened", True)
    assert project(opened, ev(T.AGENT_RUNNING)) is None  # an open PR only takes review events
    revising = project(opened, ev(T.REVIEW_RECEIVED, text="use a constant", ts=3000))
    assert (revising["stage"], revising["outcome"], revising["ended_at"], revising["review_rounds"]) == ("revising", None, None, 1)
    running = project(revising, ev(T.AGENT_RUNNING, ts=3100))
    back = project(project(running, ev(T.COST_UPDATED, cost_usd=1.9, ts=3200)), ev(T.PR_UPDATED, text="pushed abc", ts=3300))
    assert (back["stage"], back["outcome"], back["cost_usd"]) == ("pr_opened", "pr_opened", 1.9)
    merged = project(back, ev(T.PR_MERGED, ts=4000))
    assert (merged["outcome"], merged["merged_at"] is not None) == ("merged", True)
    assert project(merged, ev(T.REVIEW_RECEIVED)) is None  # merged is final


def test_a_failed_revision_returns_to_review_and_a_closed_pr_is_final():
    revising = run(ev(T.TASK_DISCOVERED), ev(T.PR_OPENED, pr_url="https://x/pull/1"), ev(T.REVIEW_RECEIVED))
    failed = project(revising, ev(T.TASK_FAILED))
    assert failed["outcome"] == "failed"
    assert project(failed, ev(T.PR_UPDATED, text="could not push"))["outcome"] == "pr_opened"
    assert project(failed, ev(T.PR_CLOSED))["outcome"] == "closed"


def test_review_events_never_revive_a_task_without_a_pr():
    assert project(run(ev(T.TASK_DISCOVERED), ev(T.TASK_FAILED)), ev(T.PR_MERGED)) is None


def test_a_merge_records_how_many_commits_people_added():
    row = run(ev(T.TASK_DISCOVERED), ev(T.PR_OPENED, pr_url="u"), ev(T.PR_MERGED, fixups=2))
    assert (row["outcome"], row["human_fixups"]) == ("merged", 2)
    assert run(ev(T.TASK_DISCOVERED), ev(T.PR_OPENED, pr_url="u"), ev(T.PR_CLOSED))["human_fixups"] is None


def test_context_and_publishing_are_stages_of_a_running_task():
    row = run(ev(T.TASK_DISCOVERED), ev(T.REPO_CHECKED_OUT), ev(T.CONTEXT_STARTED))
    assert (row["stage"], row["outcome"]) == ("context", None)
    row = run(ev(T.TASK_DISCOVERED), ev(T.PUBLISH_STARTED, text="sif/PAY-1-x"))
    assert (row["stage"], row["outcome"]) == ("publishing", None)


def test_when_the_factory_stops_following_a_pr_it_is_left_open_and_final():
    row = run(ev(T.TASK_DISCOVERED), ev(T.PR_OPENED, pr_url="u"), ev(T.REVIEW_STOPPED, text="Stopped by maria.", actor="maria"))
    assert (row["stage"], row["outcome"]) == ("left_open", "left_open") and row["ended_at"] is not None
    assert project(row, ev(T.PR_MERGED)) is None  # nobody is watching it any more
