"""The deterministic triage rules."""

import pytest

from orchestrator_worker.classify import ticket_quality

VAGUE = "Something about the login page is off, can you look?"


@pytest.mark.parametrize("title,description", [
    ("new task", "change text from nobody to somebody"),  # SCRUM-11, asked before
    ("New task", "Change all text from “nobody” to “Somebody”"),  # SCRUM-8
    ("Rename the retry setting to max_attempts", ""),  # the instruction in the title is enough
    ("Typo", "Please fix the typo 'teh' in README.md"),
    ("Billing retries", "The retry loop uses a fixed delay. It should back off exponentially and stop after five attempts."),  # the old rule still passes
])
def test_a_direct_instruction_or_a_described_outcome_needs_no_question(title, description):
    assert ticket_quality(title, description) == (True, [])


@pytest.mark.parametrize("title,description", [
    ("Task 2", ""),  # SCRUM-2
    ("Login", VAGUE),
    ("Fix", "fix it"),  # a verb with nothing to act on
    ("Bug", "change"),
])
def test_an_empty_or_vague_ticket_still_gets_the_question(title, description):
    ok, missing = ticket_quality(title, description)
    assert not ok and missing


def test_epics_still_need_a_description_but_no_acceptance_criteria():
    assert ticket_quality("Checkout", "", "epic") == (False, ["a description of at least a couple of sentences"])
    assert ticket_quality("Checkout", "Customers can pay with saved cards and see their receipts in the app.", "epic") == (True, [])
