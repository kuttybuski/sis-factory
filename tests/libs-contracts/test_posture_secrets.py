import pytest

from sis_contracts.posture import enforce, problems
from sis_contracts.secrets import env_secret, load_secret_files

T = "x" * 32


def test_outside_production_nothing_is_required():
    assert problems("portal-api", {}) == []


def test_production_refuses_weak_or_shared_tokens_and_demo_settings():
    env = {"SIS_ENV": "production", "SIS_INGEST_TOKEN": T, "SIS_INTERNAL_TOKEN": "y" * 32, "SIS_DASHBOARD_TOKEN": T, "SIS_SECRET_KEY": "k",
           "DATABASE_URL": "postgresql://sis:sis-local-dev@postgres/sis"}
    found = problems("portal-api", env)
    assert any("must differ" in p for p in found) and any("development password" in p for p in found)
    decided = {"SIS_LESSON_AUTO_APPROVE": "false", "SIS_RETENTION_DAYS": "90", "SIS_AUDIT_RETENTION_DAYS": "0"}
    assert problems("portal-api", {**env, **decided, "SIS_DASHBOARD_TOKEN": "z" * 32, "DATABASE_URL": "postgresql://sis:s3cret@db/sis"}) == []


def test_production_needs_people_to_approve_lessons_and_a_retention_decision():
    env = {"SIS_ENV": "production", "SIS_INGEST_TOKEN": T, "SIS_INTERNAL_TOKEN": "y" * 32, "SIS_DASHBOARD_TOKEN": "z" * 32, "SIS_SECRET_KEY": "k"}
    found = problems("portal-api", env)
    assert any("SIS_LESSON_AUTO_APPROVE must be false" in p for p in found)
    assert any("SIS_RETENTION_DAYS must be set" in p for p in found) and any("SIS_AUDIT_RETENTION_DAYS must be set" in p for p in found)
    assert problems("portal-api", {**env, "SIS_LESSON_AUTO_APPROVE": "false", "SIS_RETENTION_DAYS": "0", "SIS_AUDIT_RETENTION_DAYS": "365"}) == []  # 0 is a decision too
    assert any("must be set" in p for p in problems("portal-api", {**env, "SIS_LESSON_AUTO_APPROVE": "false", "SIS_RETENTION_DAYS": "", "SIS_AUDIT_RETENTION_DAYS": "x"}))
    assert any("at least 24" in p for p in problems("jira-connector", {"SIS_ENV": "production", "SIS_INGEST_TOKEN": "short", "SIS_INTERNAL_TOKEN": T}))


def test_production_git_broker_needs_an_app_and_protected_branches():
    env = {"SIS_ENV": "production", "SIS_INTERNAL_TOKEN": T, "GITHUB_TOKEN": "ghp_x", "SIS_REQUIRE_PROTECTED_BRANCH": "false", "SIS_ALLOW_DEPENDENCY_CHANGES": "true"}
    assert len(problems("git-broker", env)) == 3
    assert problems("git-broker", {"SIS_ENV": "production", "SIS_INTERNAL_TOKEN": T, "GITHUB_APP_ID": "1"}) == []
    assert problems("orchestrator-worker", {"SIS_ENV": "production", "SIS_INGEST_TOKEN": T, "SIS_INTERNAL_TOKEN": T, "SIS_AGENT": "scripted"})


def test_enforce_exits_with_the_reasons(monkeypatch):
    monkeypatch.setenv("SIS_ENV", "production")
    monkeypatch.delenv("SIS_INTERNAL_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="SIS_INTERNAL_TOKEN"):
        enforce("git-broker")


def test_secrets_can_come_from_mounted_files(tmp_path):
    f = tmp_path / "token"
    f.write_text("from-vault\n")
    env = {"SIS_INGEST_TOKEN_FILE": str(f), "SIS_INTERNAL_TOKEN": "direct", "SIS_INTERNAL_TOKEN_FILE": str(f)}
    assert load_secret_files(env) == ["SIS_INGEST_TOKEN"] and env["SIS_INGEST_TOKEN"] == "from-vault"
    assert env_secret("SIS_INTERNAL_TOKEN", env) == "direct" and env_secret("NOPE", env) == ""
