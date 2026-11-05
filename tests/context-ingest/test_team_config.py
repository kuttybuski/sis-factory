"""A source is synced with its team's own Jira and GitHub connections when it has them, and with the shared credentials otherwise."""

import pytest
from context_ingest import sync
from sif_contracts.connections import ConnectionsUnavailable, GithubCreds, JiraCreds


class Creds:
    def __init__(self, jira=None, github=None, error=None):
        self.j, self.g, self.error, self.asked = jira, github, error, []

    async def jira(self, team):
        self.asked.append(("jira", team))
        if self.error:
            raise self.error
        return self.j

    async def github(self, team):
        self.asked.append(("github", team))
        return self.g


def shared(**kw):
    return sync.Config(confluence_url="https://shared.atlassian.net", confluence_email="shared@x", confluence_token="shared-jira", github_token="shared-gh", **kw)


async def test_a_team_connection_replaces_the_shared_credentials():
    cfg = shared(credentials=Creds(JiraCreds("https://team.atlassian.net", "bot@team", "team-jira"), GithubCreds("team-gh", "https://api.ghe.test")))
    out = await sync.config_for(cfg, "Payments")
    assert (out.confluence_url, out.confluence_email, out.confluence_token) == ("https://team.atlassian.net", "bot@team", "team-jira")
    assert (out.github_token, out.github_api_url) == ("team-gh", "https://api.ghe.test")
    assert cfg.confluence_token == "shared-jira"  # the shared configuration object is not changed


async def test_each_provider_falls_back_on_its_own():
    out = await sync.config_for(shared(credentials=Creds(jira=None, github=GithubCreds("team-gh"))), "Payments")
    assert (out.confluence_token, out.github_token) == ("shared-jira", "team-gh")


async def test_no_team_or_no_credential_source_means_shared():
    cfg = shared(credentials=Creds())
    assert await sync.config_for(cfg, None) is cfg and cfg.credentials.asked == []
    plain = shared()
    assert await sync.config_for(plain, "Payments") is plain


async def test_an_unavailable_portal_stops_the_sync_instead_of_using_the_shared_account():
    with pytest.raises(ConnectionsUnavailable):
        await sync.config_for(shared(credentials=Creds(error=ConnectionsUnavailable("down"))), "Payments")


async def test_run_source_uses_the_teams_github_token(monkeypatch):
    used = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    monkeypatch.setattr(sync.github, "client", lambda token, base: used.append((token, base)) or Client())

    async def no_docs(*a, **k):
        return sync.db.SyncStats()

    monkeypatch.setattr(sync.db, "sync_docs", no_docs)
    monkeypatch.setattr(sync.github, "fetch_repo_docs", lambda *a, **k: None)
    cfg = shared(credentials=Creds(github=GithubCreds("team-gh", "https://api.ghe.test")))
    await sync.run_source(None, {"kind": "repo_docs", "key": "acme/api", "team": "Payments", "id": 1}, None, cfg)
    assert used == [("team-gh", "https://api.ghe.test")]
