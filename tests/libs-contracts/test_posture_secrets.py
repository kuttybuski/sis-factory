import pytest

from sif_contracts.posture import enforce, problems
from sif_contracts.secrets import env_secret, load_secret_files

T = "x" * 32


def test_outside_production_nothing_is_required():
    assert problems("portal-api", {}) == []


def test_production_refuses_weak_or_shared_tokens_and_demo_settings():
    env = {"SIF_ENV": "production", "SIF_INGEST_TOKEN": T, "SIF_INTERNAL_TOKEN": "y" * 32, "SIF_DASHBOARD_TOKEN": T, "SIF_SECRET_KEY": "k",
           "DATABASE_URL": "postgresql://sif:sif-local-dev@postgres/sif"}
    found = problems("portal-api", env)
    assert any("must differ" in p for p in found) and any("development password" in p for p in found)
    decided = {"SIF_LESSON_AUTO_APPROVE": "false", "SIF_RETENTION_DAYS": "90", "SIF_AUDIT_RETENTION_DAYS": "0"}
    assert problems("portal-api", {**env, **decided, "SIF_DASHBOARD_TOKEN": "z" * 32, "DATABASE_URL": "postgresql://sif:s3cret@db/sif"}) == []


def test_production_needs_people_to_approve_lessons_and_a_retention_decision():
    env = {"SIF_ENV": "production", "SIF_INGEST_TOKEN": T, "SIF_INTERNAL_TOKEN": "y" * 32, "SIF_DASHBOARD_TOKEN": "z" * 32, "SIF_SECRET_KEY": "k"}
    found = problems("portal-api", env)
    assert any("SIF_LESSON_AUTO_APPROVE must be false" in p for p in found)
    assert any("SIF_RETENTION_DAYS must be set" in p for p in found) and any("SIF_AUDIT_RETENTION_DAYS must be set" in p for p in found)
    assert problems("portal-api", {**env, "SIF_LESSON_AUTO_APPROVE": "false", "SIF_RETENTION_DAYS": "0", "SIF_AUDIT_RETENTION_DAYS": "365"}) == []  # 0 is a decision too
    assert any("must be set" in p for p in problems("portal-api", {**env, "SIF_LESSON_AUTO_APPROVE": "false", "SIF_RETENTION_DAYS": "", "SIF_AUDIT_RETENTION_DAYS": "x"}))
    assert any("at least 24" in p for p in problems("jira-connector", {"SIF_ENV": "production", "SIF_INGEST_TOKEN": "short", "SIF_INTERNAL_TOKEN": T}))


def test_production_git_broker_needs_an_app_and_protected_branches():
    env = {"SIF_ENV": "production", "SIF_INTERNAL_TOKEN": T, "GITHUB_TOKEN": "ghp_x", "SIF_REQUIRE_PROTECTED_BRANCH": "false", "SIF_ALLOW_DEPENDENCY_CHANGES": "true"}
    assert len(problems("git-broker", env)) == 3
    assert problems("git-broker", {"SIF_ENV": "production", "SIF_INTERNAL_TOKEN": T, "GITHUB_APP_ID": "1"}) == []
    assert problems("orchestrator-worker", {"SIF_ENV": "production", "SIF_INGEST_TOKEN": T, "SIF_INTERNAL_TOKEN": T, "SIF_AGENT": "scripted"})


def test_enforce_exits_with_the_reasons(monkeypatch):
    monkeypatch.setenv("SIF_ENV", "production")
    monkeypatch.delenv("SIF_INTERNAL_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="SIF_INTERNAL_TOKEN"):
        enforce("git-broker")


def test_secrets_can_come_from_mounted_files(tmp_path):
    f = tmp_path / "token"
    f.write_text("from-vault\n")
    env = {"SIF_INGEST_TOKEN_FILE": str(f), "SIF_INTERNAL_TOKEN": "direct", "SIF_INTERNAL_TOKEN_FILE": str(f)}
    assert load_secret_files(env) == ["SIF_INGEST_TOKEN"] and env["SIF_INGEST_TOKEN"] == "from-vault"
    assert env_secret("SIF_INTERNAL_TOKEN", env) == "direct" and env_secret("NOPE", env) == ""
