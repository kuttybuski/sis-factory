from datetime import datetime, timedelta, timezone

import pytest

from sis_contracts import Settings
from sis_contracts.lessons import (Command, Decision, Lesson, Role, Scope, Source, Status, TaskContext, can_forget, clean_text,
                                   decide, parse_command, render_rules, select_lessons, text_error)

S = Settings(_env_file=None)
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def lesson(i, text, scope=Scope.REPO, value="acme/api", **kw):
    return Lesson(id=i, text=text, scope=scope, scope_value=value, repo=kw.pop("repo", "acme/api"),
                  created_at=kw.pop("created_at", NOW - timedelta(days=i)), **kw)


# --- parsing
@pytest.mark.parametrize("comment", [
    "@sis remember always run the migration linter before opening a PR",
    "@sis-remember always run the migration linter before opening a PR",
    "@SIS: Remember - always run the migration linter before opening a PR",
    "Thanks! @sis remember: always run the migration linter before opening a PR",
])
def test_remember_variants(comment):
    c = parse_command(comment, S)
    assert c == Command(action="remember", text="always run the migration linter before opening a PR")


def test_scope_and_ttl_options_are_extracted_from_the_text():
    c = parse_command("@sis remember use the v2 client scope=path:src/billing/** ttl=90d", S)
    assert (c.scope, c.scope_value, c.ttl_days, c.text) == (Scope.PATH, "src/billing/**", 90, "use the v2 client")
    assert parse_command("@sis remember keep PRs small scope=team", S).scope is Scope.TEAM
    assert parse_command("@sis remember never log tokens scope=GLOBAL", S).scope is Scope.GLOBAL


def test_forget_takes_an_id():
    assert parse_command("@sis forget L12", S) == Command(action="forget", lesson_id=12)
    assert parse_command("@sis-forget 7", S).lesson_id == 7
    assert parse_command("@sis forget everything", S) is None


@pytest.mark.parametrize("comment", ["@sis approve", "please remember to review", "email x@sis.com remember this", "@sisfy remember the milk"])
def test_other_comments_are_not_commands(comment):
    assert parse_command(comment, S) is None


def test_command_words_follow_settings():
    s = Settings(_env_file=None, cmd_remember="learn")
    assert parse_command("@sis learn use small PRs always", s).action == "remember"
    assert parse_command("@sis remember use small PRs always", s) is None


def test_text_is_flattened_and_markup_removed():
    out = clean_text("line one\nline two\n# heading `code` <tag> | pipe")
    assert out == "line one line two heading code tag pipe"


def test_length_limits():
    assert text_error("short", S) is not None
    assert text_error("a" * 501, S) is not None
    assert text_error("always run the linter before opening a pull request", S) is None


# --- approval by role
@pytest.mark.parametrize("scope,role,expected", [
    (Scope.REPO, Role.MAINTAINER, Decision.APPROVED),
    (Scope.PATH, Role.MAINTAINER, Decision.APPROVED),
    (Scope.REPO, Role.MEMBER, Decision.CANDIDATE),
    (Scope.TEAM, Role.MAINTAINER, Decision.CANDIDATE),
    (Scope.TEAM, Role.TEAM_LEAD, Decision.APPROVED),
    (Scope.GLOBAL, Role.TEAM_LEAD, Decision.CANDIDATE),
    (Scope.GLOBAL, Role.ADMIN, Decision.APPROVED),
    (Scope.GLOBAL, Role.NONE, Decision.DENIED),
])
def test_role_based_approval(scope, role, expected):
    assert decide(scope, role, Source.HUMAN_COMMENT, S) is expected


def test_only_clean_human_comments_are_ever_auto_approved():
    for src in (Source.AGENT_SUGGESTION, Source.PR_REVIEW, Source.MINED):
        assert decide(Scope.REPO, Role.ADMIN, src, S) is Decision.CANDIDATE
    assert decide(Scope.REPO, Role.ADMIN, Source.HUMAN_COMMENT, S, flagged=True) is Decision.CANDIDATE
    assert decide(Scope.REPO, Role.ADMIN, Source.HUMAN_COMMENT, Settings(_env_file=None, lesson_auto_approve=False)) is Decision.CANDIDATE
    assert decide(Scope.REPO, Role.NONE, Source.MINED, S) is Decision.DENIED


def test_forget_permissions():
    assert can_forget("ann", Role.MEMBER, "ann")
    assert not can_forget("bob", Role.MEMBER, "ann")
    assert can_forget("bob", Role.MAINTAINER, "ann")
    assert not can_forget("ann", Role.NONE, "ann")


# --- selection and delivery
def test_scope_matching():
    ctx = TaskContext(repo="acme/api", team="Payments", paths=["src/billing/invoice.py"])
    assert [l.id for l in select_lessons([
        lesson(1, "global rule", Scope.GLOBAL, None, repo=None),
        lesson(2, "team rule", Scope.TEAM, "Payments", repo=None),
        lesson(3, "other team", Scope.TEAM, "Mobile", repo=None),
        lesson(4, "repo rule", Scope.REPO, "acme/api"),
        lesson(5, "other repo", Scope.REPO, "acme/web", repo="acme/web"),
        lesson(6, "path rule", Scope.PATH, "src/billing/*"),
        lesson(7, "path elsewhere", Scope.PATH, "src/auth/*"),
        lesson(8, "path same glob other repo", Scope.PATH, "src/billing/*", repo="acme/web"),
    ], ctx, NOW, S)] == [6, 4, 2, 1]  # most specific first


def test_only_approved_unexpired_lessons_are_selected():
    ctx = TaskContext(repo="acme/api")
    ls = [lesson(1, "ok"), lesson(2, "pending", status=Status.CANDIDATE), lesson(3, "off", status=Status.DISABLED),
          lesson(4, "rejected", status=Status.REJECTED), lesson(5, "old", expires_at=NOW - timedelta(days=1)),
          lesson(6, "valid", expires_at=NOW + timedelta(days=1))]
    assert [l.id for l in select_lessons(ls, ctx, NOW, S)] == [1, 6]


def test_relevance_to_the_task_orders_lessons_within_a_scope():
    ctx = TaskContext(repo="acme/api", query="fix the invoice rounding bug")
    ls = [lesson(1, "prefer small pull requests"), lesson(2, "invoice totals use banker's rounding")]
    assert [l.id for l in select_lessons(ls, ctx, NOW, S)] == [2, 1]


def test_count_and_token_budget_are_respected():
    ctx = TaskContext(repo="acme/api")
    many = [lesson(i, f"rule number {i} " + "x" * 20) for i in range(1, 30)]
    assert len(select_lessons(many, ctx, NOW, S)) == S.lesson_max_in_prompt
    tight = Settings(_env_file=None, lesson_token_budget=40)
    assert 1 <= len(select_lessons(many, ctx, NOW, tight)) <= 2
    big, small = lesson(1, "y" * 480), lesson(2, "short rule here")
    assert [l.id for l in select_lessons([big, small], ctx, NOW, tight)] == [2]  # skips the one that does not fit


def test_rendered_block_lists_ids_and_is_empty_without_lessons():
    assert render_rules([]) == ""
    out = render_rules([lesson(12, "run the linter first")])
    assert out.startswith("## Team rules") and "- [L12] run the linter first" in out


def test_machine_lessons_are_held_back_from_a_stable_random_share_of_tasks():
    from sis_contracts.lessons import held_out

    mined, taught = lesson(1, "Run unit-tests before finishing", source=Source.MINED), lesson(2, "Use Decimal for money")
    held = [t for t in (f"PAY-{i}" for i in range(1000)) if held_out(t, mined, 20)]
    assert 150 < len(held) < 250  # about a fifth
    assert all(held_out(t, mined, 20) for t in held[:20])  # the same task always lands in the same group, across review rounds
    assert not any(held_out(f"PAY-{i}", taught, 20) for i in range(200))  # what a person taught is always delivered
    assert not any(held_out(f"PAY-{i}", mined, 0) for i in range(200))
    other = lesson(3, "Another mined rule", source=Source.PR_REVIEW)
    assert {t for t in (f"PAY-{i}" for i in range(1000)) if held_out(t, other, 20)} != set(held)  # independent per lesson
