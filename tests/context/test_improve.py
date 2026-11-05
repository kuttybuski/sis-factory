from sis_context.improve import (Effect, ci_candidates, eval_counts, feedback_from_signals, intake_gaps, lesson_effects, novel, parse_review_text,
                                 proposals, quality, recurring_despite, repo_quality, review_notes_by_repo)
from sis_context.learn import mine_review_lessons

ROUND = ("Review from ann (src/pay.py:12):\nAlways use Decimal for money amounts, never float.\n\n"
         "Review from bo:\nPlease add a test for the empty cart.\n\nCI:\nunit-tests failed. 2 failed\n\nlint failed. E501")


def ev(task, type, payload, repo="acme/api", team="Payments"):
    return {"task_id": task, "repo": repo, "team": team, "type": type, "ts": None, "payload": payload}


def test_a_revision_round_splits_into_review_items_and_ci_failures():
    items = parse_review_text(ROUND)
    assert [(k, a, p) for k, a, p, _ in items] == [("review", "ann", "src/pay.py:12"), ("review", "bo", ""), ("ci", "CI", "")]
    assert items[0][3] == "Always use Decimal for money amounts, never float." and "lint failed" in items[2][3]
    assert parse_review_text("") == [] and parse_review_text("no headers here") == []


def test_signals_become_feedback_including_what_people_fixed_by_hand():
    fb = feedback_from_signals([ev("PAY-1", "review.received", {"text": ROUND}),
                                ev("PAY-1", "pr.merged", {"fixups": 1, "text": "Merged with commits by people:\n- use Decimal for money (ann)"}),
                                ev("PAY-2", "pr.merged", {"fixups": 0})])
    assert [f.kind for f in fb] == ["review", "review", "ci", "fixup"]
    assert fb[-1].body == "use Decimal for money" and fb[-1].author == "ann"


def test_the_same_remark_on_several_factory_prs_becomes_a_candidate_lesson():
    fb = feedback_from_signals([ev("PAY-1", "review.received", {"text": "Review from ann:\nAlways use Decimal for money amounts, never float."}),
                                ev("PAY-2", "review.received", {"text": "Review from bo:\nYou should always use Decimal for money amounts."}),
                                ev("PAY-3", "review.received", {"text": "Review from cy (a.py):\nRename this variable please, it is unclear to me."})])
    notes = review_notes_by_repo(fb)
    found = mine_review_lessons(notes["acme/api"], min_count=2)
    assert len(found) == 1 and "Decimal" in found[0].text and found[0].count == 2


def test_a_check_that_keeps_failing_becomes_a_candidate_to_run_it_first():
    fb = feedback_from_signals([ev(f"PAY-{i}", "review.received", {"text": "CI:\nunit-tests failed. boom"}) for i in (1, 2, 2)]
                               + [ev("PAY-9", "review.received", {"text": "CI:\nlint failed. E501"})])
    found = ci_candidates(fb)
    assert [(c.repo, c.check, c.tasks) for c in found] == [("acme/api", "unit-tests", ["PAY-1", "PAY-2"])]
    assert "unit-tests" in found[0].text


def test_novel_skips_near_duplicates_of_known_lessons():
    assert not novel("Always use Decimal for money amounts", ["always use decimal for money amounts!"])
    assert novel("Add a test for every bug fix", ["Always use Decimal for money amounts"])


def test_quality_rewards_work_merged_as_delivered():
    assert quality("merged", 0, 0) == 1.0 and quality("merged", 2, 0) == 0.5 and quality("merged", 0, 1) == 0.5
    assert quality("closed", 0, 0) == 0.0 and quality("failed", None, None) == 0.0


def rows(lesson, applied, merged_clean, total):
    return [{"lesson_id": lesson, "task_id": f"{lesson}-{applied}-{i}", "applied": applied, "outcome": "merged" if i < merged_clean else "closed",
             "review_rounds": 0, "human_fixups": 0} for i in range(total)]


def test_a_lesson_is_judged_only_against_its_comparison_group_and_only_with_enough_tasks():
    few = lesson_effects(rows(1, True, 2, 5) + rows(1, False, 5, 5))
    assert few[0].verdict == "collecting" and "5 with, 5 without" in few[0].note
    hurt = lesson_effects(rows(2, True, 3, 20) + rows(2, False, 16, 20))[0]
    assert hurt.verdict == "hurting" and hurt.merge_rate_applied == 0.15 and hurt.merge_rate_held == 0.8
    helps = lesson_effects(rows(3, True, 18, 20) + rows(3, False, 6, 20))[0]
    assert helps.verdict == "helping"
    noise = lesson_effects(rows(4, True, 11, 20) + rows(4, False, 12, 20))[0]
    assert noise.verdict == "no_effect"  # one task's difference is chance, not a reason to retire a rule


def test_the_online_comparison_feeds_the_eval_gate_on_equal_groups():
    e = Effect(lesson_id=1, applied=30, held=10, merge_rate_applied=0.9, merge_rate_held=0.5, quality_applied=0.9, quality_held=0.5, verdict="helping", note="")
    assert eval_counts(e) == (10, 5, 9)


def test_a_lesson_reviewers_still_have_to_ask_for_is_reported():
    fb = feedback_from_signals([ev(t, "review.received", {"text": "Review from ann:\nPlease use Decimal for money amounts in billing."}) for t in ("A", "B", "C")])
    lesson = {"id": 7, "text": "Use Decimal for money amounts in billing code."}
    found = recurring_despite([lesson], fb, {7: {"A", "B"}})
    assert found and found[0].tasks == ["A", "B"]  # C did not get the lesson, so it does not count against it
    assert recurring_despite([lesson], fb, {7: {"A"}}) == []


def test_intake_gaps_count_what_tickets_leave_out():
    sig = [ev("A", "agent.asked_question", {"reason": "question", "text": "Before I start, please tell me which repository and the acceptance criteria."}),
           ev("B", "jira.bounced", {"text": "Ticket needs more detail: the acceptance criteria"}),
           ev("C", "agent.asked_question", {"reason": "budget", "text": "Before I start, please tell me more."})]
    gaps = intake_gaps(sig)
    assert gaps[0] == ("the acceptance criteria", "Payments", 2) and ("which repository", "Payments", 1) in gaps


def test_proposals_turn_findings_into_actions_for_people():
    tasks = [{"repo": "acme/api", "outcome": "merged", "review_rounds": 1, "human_fixups": 0}] * 8 + [{"repo": "acme/api", "outcome": "merged", "review_rounds": 0, "human_fixups": 0}] * 2
    q = repo_quality(tasks, min_n=10)
    assert q == [{"repo": "acme/api", "decided": 10, "merged": 10, "as_delivered": 2, "first_time_right": 0.2, "enough": True}]
    flat = Effect(lesson_id=3, applied=40, held=35, merge_rate_applied=0.5, merge_rate_held=0.5, quality_applied=0.5, quality_held=0.5, verdict="no_effect", note="n/a")
    out = proposals(gaps=[("the acceptance criteria", "Payments", 4)], recurrences=[], effects=[flat], lessons_by_id={3: {"text": "x"}},
                    quality_rows=q, feedback=[], waiting=2, min_n=10)
    assert [p.kind for p in out] == ["intake", "no_effect", "quality", "review_candidates"]
    assert "issue template for Payments" in out[0].action


def test_team_knowledge_is_drafted_from_docs_and_readmes():
    from sis_context.improve import glossary_from_docs, systems_from_readmes

    chunks = [{"text": "# Terms\n- **Settlement**: the nightly transfer of captured payments\nSee: http://x\nPayout: money sent to a merchant bank account", "title": "Glossary", "url": "https://wiki/g"}]
    g = glossary_from_docs(chunks, known=["payout"])
    assert [s.text for s in g] == ["Settlement: the nightly transfer of captured payments"] and g[0].evidence == ["https://wiki/g"]
    s = systems_from_readmes(["acme/billing-api", "acme/ledger"], {"acme/billing-api": "# Billing API\n\nIssues invoices. Runs payments nightly."}, known=["ledger: the books"])
    assert [x.text for x in s] == ["billing-api: Issues invoices."] and s[0].kind == "from_docs"
    blank = systems_from_readmes(["acme/no-readme"], {}, known=[])
    assert blank[0].kind == "missing"  # nothing to go on: the page asks for a line instead of offering filler


def test_lessons_learned_across_the_teams_repos_are_offered_as_team_rules():
    from sis_context.improve import promotions

    ls = [{"id": 1, "status": "approved", "scope": "repo", "repo": "acme/a", "text": "Always use Decimal for money amounts"},
          {"id": 2, "status": "approved", "scope": "repo", "repo": "acme/b", "text": "Always use Decimal for all money amounts"},
          {"id": 3, "status": "approved", "scope": "repo", "repo": "acme/a", "text": "Prefer the shared retry helper in this repo"},
          {"id": 4, "status": "approved", "scope": "team", "scope_value": "Payments", "repo": None, "text": "Tag every PR with the ticket key"},
          {"id": 5, "status": "approved", "scope": "repo", "repo": "other/x", "text": "Always use Decimal for money amounts"}]
    out = promotions(ls, "Payments", ["acme/a", "acme/b"], rules=[])
    assert [(p.text, p.evidence) for p in out] == [("Always use Decimal for money amounts", ["L1", "L2"]), ("Tag every PR with the ticket key", ["L4"])]
    assert promotions(ls, "Payments", ["acme/a", "acme/b"], rules=["Always use Decimal for money amounts."])[0].text.startswith("Tag")  # already written


def test_written_rules_reviewers_keep_raising_are_flagged():
    from sis_context.improve import rules_still_raised

    fb = feedback_from_signals([ev(t, "review.received", {"text": "Review from ann:\nPlease never log the card number here."}) for t in ("A", "B")])
    out = rules_still_raised(["Never log card numbers or CVV."], fb, "Payments")
    assert out and out[0].kind == "still_raised" and out[0].evidence == ["A", "B"]
    assert rules_still_raised(["Never log card numbers or CVV."], fb, "Other") == []
