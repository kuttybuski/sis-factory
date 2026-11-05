from sis_context.learn import (EvalReport, ReviewNote, calibrate_budgets, candidate_sentences, mine_review_lessons, passes_gate, percentile,
                               qa_pairs_from_events, summarize_failures)


def ev(type, text=None, **kw):
    return {"type": type, "text": text, "ts": 1, **kw}


def test_qa_pairs_match_each_question_with_the_next_answer():
    pairs = qa_pairs_from_events("PAY-1", [
        ev("agent.asked_question", "Which module is canonical?", reason="question"), ev("agent.answered", "@sis Use billing/address.py."),
        ev("agent.asked_question", "Backoff or fixed delay?"), ev("agent.answered", "Backoff"),
    ])
    assert [(p.question, p.answer) for p in pairs] == [("Which module is canonical?", "Use billing/address.py."), ("Backoff or fixed delay?", "Backoff")]


def test_budget_questions_and_unanswered_questions_are_not_knowledge():
    assert qa_pairs_from_events("T", [ev("agent.asked_question", "Budget reached", reason="budget"), ev("agent.answered", "@sis approve")]) == []
    assert qa_pairs_from_events("T", [ev("agent.asked_question", "Which?"), ev("wait.timed_out"), ev("agent.answered", "late answer")]) == []


def test_candidate_sentences_are_imperative_and_not_questions_or_code():
    body = ("Nice work. Please use exponential backoff instead of a fixed delay here.\n"
            "Why did you choose five attempts?\n```\nshould never run this code block\n```\nWe always log the request id in handlers.")
    assert candidate_sentences(body) == ["Please use exponential backoff instead of a fixed delay here.", "We always log the request id in handlers."]


def test_recurring_feedback_becomes_a_candidate_and_one_offs_do_not():
    notes = [ReviewNote(pr="1", reviewer="bob", body="Please use exponential backoff instead of a fixed delay for retries."),
             ReviewNote(pr="2", reviewer="cy", body="You should use exponential backoff rather than a fixed delay for retries, please."),
             ReviewNote(pr="3", reviewer="bob", body="We never commit generated files to this repository.")]
    found = mine_review_lessons(notes)
    assert len(found) == 1 and found[0].count == 2 and found[0].reviewers == ["bob", "cy"] and "backoff" in found[0].text
    assert len(mine_review_lessons(notes, min_count=1)) == 2


def test_the_same_pr_raising_a_point_twice_counts_once():
    notes = [ReviewNote(pr="1", reviewer="bob", body="Please use exponential backoff instead of a fixed delay."),
             ReviewNote(pr="1", reviewer="bob", body="Please use exponential backoff instead of a fixed delay.")]
    assert mine_review_lessons(notes) == []


def test_percentile():
    assert percentile([], 0.9) == 0 and percentile([5], 0.9) == 5
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3 and round(percentile([1, 2, 3, 4, 5], 0.9), 2) == 4.6


def rows(group, costs, outcome="pr_opened"):
    return [{"group": group, "cost_usd": c, "outcome": outcome} for c in costs]


def test_budget_suggestions_use_successful_task_costs_with_headroom():
    out = {s.group: s for s in calibrate_budgets(rows("Payments", [0.2, 0.3, 0.25, 0.4, 0.3, 0.35, 0.28, 0.32]) + rows("Mobile", [1.0]), current=2.0)}
    assert out["Payments"].suggested_budget < 1.0 and "too high" in out["Payments"].note
    assert out["Mobile"].suggested_budget == 2.0 and "Need at least" in out["Mobile"].note


def test_tight_budgets_are_called_out_and_bounced_tasks_ignored():
    r = rows("Platform", [1.9, 2.0, 1.8, 1.95, 2.0, 1.7, 1.85, 2.0]) + rows("Platform", [0.0] * 20, outcome="bounced")
    s = calibrate_budgets(r, current=2.0)[0]
    assert s.samples == 8 and s.suggested_budget > 2.0 and "too tight" in s.note


def test_failure_summary_groups_errors_and_gives_advice():
    tasks = [{"outcome": "timed_out"}] * 3 + [{"outcome": "pr_opened"}] * 5 + [{"outcome": "failed"}]
    r = summarize_failures(tasks, ["Error: tests still failing after 2 CI rounds", "Error: tests still failing after 3 CI rounds", "Read src/a.py"])
    assert r.total == 9 and r.by_outcome["timed_out"] == 3
    assert r.top_errors == [("tests still failing after N CI rounds", 2)]
    assert any("timed_out" in f for f in r.findings) and not any("'failed'" in f for f in r.findings)


def test_failure_summary_is_quiet_with_too_little_data():
    assert summarize_failures([{"outcome": "failed"}], []).findings == []


def test_eval_gate():
    assert passes_gate(EvalReport(n=20, baseline_pass=15, candidate_pass=16))
    assert not passes_gate(EvalReport(n=20, baseline_pass=15, candidate_pass=14))
    assert not passes_gate(EvalReport(n=5, baseline_pass=5, candidate_pass=5))  # too few samples
    assert passes_gate(EvalReport(n=20, baseline_pass=15, candidate_pass=14), max_regression=1)
