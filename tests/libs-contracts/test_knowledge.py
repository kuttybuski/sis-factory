from datetime import UTC, datetime, timedelta

from sif_contracts.knowledge import entries, for_task, lines, mentioned, stale, text_error

PAGE = {
    "rules": "- Never log card numbers or CVV.\n- Migrations must be backward compatible.",
    "done": "Unit tests, a feature flag for user-facing changes, changelog entry.",
    "glossary": "Settlement: the nightly transfer of captured payments to merchants\n- **Chargeback**: a payment the cardholder disputed\nnot an entry line",
    "systems": "billing-api: invoices and payment runs; owned by us\nledger: the books; owned by Finance",
    "process": "Trunk-based; release on Tuesdays.",
}


def test_entries_lines_and_mentions():
    assert entries(PAGE["glossary"]) == [("Settlement", "the nightly transfer of captured payments to merchants"), ("Chargeback", "a payment the cardholder disputed")]
    assert lines(PAGE["rules"]) == ["Never log card numbers or CVV.", "Migrations must be backward compatible."]
    assert mentioned("billing-api", "The Billing API returns 500") and mentioned("billing-api", "fix billing-api timeouts")
    assert not mentioned("ledger", "ledgers and more") and not mentioned("api", "rapid")


def test_an_agent_gets_every_rule_and_only_the_terms_and_systems_its_ticket_mentions():
    md = for_task("Payments", PAGE, "Settlement totals are wrong after a chargeback in billing-api")
    assert md.startswith("## Team knowledge (Payments, written by the team)") and "stop and ask" in md
    assert "Never log card numbers" in md and "backward compatible" in md and "feature flag" in md
    assert "- Settlement: the nightly" in md and "- Chargeback:" in md and "- billing-api:" in md and "ledger" not in md
    assert "release on Tuesdays" in md
    plain = for_task("Payments", PAGE, "Update the README")
    assert "Glossary" not in plain and "Never log card numbers" in plain
    assert for_task("Payments", {}, "anything") == ""


def test_the_budget_keeps_the_most_important_parts():
    md = for_task("P", {**PAGE, "notes": "x" * 5000}, "Settlement", budget_chars=400)
    assert "Never log card numbers" in md and "x" * 100 not in md


def test_limits_and_staleness():
    assert text_error("rules", "a" * 3001) and text_error("nope", "x") and text_error("rules", "ok") is None
    now = datetime(2026, 10, 31, tzinfo=UTC)
    assert stale(None, now) and stale(now - timedelta(days=91), now) and not stale(now - timedelta(days=10), now)
