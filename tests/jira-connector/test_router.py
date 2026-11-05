"""Per-team Jira accounts: which client acts for which project, what each searches, and what happens when the portal cannot say."""

import pytest
from jira_connector import activities
from jira_connector.client import JiraClient
from jira_connector.router import JiraRouter, NoConnection
from sif_contracts.connections import ConnectionsUnavailable, JiraCreds

TEAMS = [{"name": "Payments", "jira_projects": ["PAY", "BILL"]}, {"name": "Mobile", "jira_projects": ["MOB"]}, {"name": "Empty", "jira_projects": []}]


class Creds:
    def __init__(self, by_team=None, error=None):
        self.by_team, self.error = by_team or {}, error

    async def jira(self, team):
        if self.error:
            raise self.error
        return self.by_team.get(team)


class Recorder(JiraClient):
    """A client that records writes instead of calling Jira."""

    def __init__(self, name, account="acct"):
        self.name, self.account, self.calls = name, account, []

    async def myself(self):
        return {"accountId": f"{self.account}-{self.name}"}

    async def add_comment(self, key, text):
        self.calls.append(("comment", key))

    async def update_labels(self, key, add, remove):
        self.calls.append(("labels", key))

    async def create_issue(self, project, summary, description, issue_type, parent_key=None, labels=None):
        self.calls.append(("create", project))
        return f"{project}-1"


def router(shared=True, teams=None, **kw):
    r = JiraRouter(shared=Recorder("shared") if shared else None, credentials=Creds(teams or {}), **kw)
    return r


PAY = JiraCreds("https://pay.atlassian.net", "bot@pay", "tok-pay")


async def test_a_team_with_a_connection_gets_its_own_client_for_its_projects_only():
    r = router(teams={"Payments": PAY})
    await r.refresh(TEAMS)
    own = r.for_key("PAY-7")
    assert own is r.for_key("BILL-2") and own is not r.shared and isinstance(own, JiraClient)
    assert own.http.base_url.host == "pay.atlassian.net"
    assert r.for_key("MOB-1") is r.shared and r.for_key("OPS-9") is r.shared  # no connection of their own: the shared account
    assert r.status == {"Payments": "own connection", "Mobile": "shared"}


async def test_searches_are_split_so_no_account_reads_another_accounts_projects():
    r = router(teams={"Payments": PAY})
    await r.refresh(TEAMS)
    scopes = {s.name: s for s in await r.scopes()}
    assert scopes["Payments"].jql == 'project in ("BILL", "PAY")'
    assert scopes["(shared)"].jql == 'project not in ("BILL", "PAY")' and scopes["(shared)"].client is r.shared


async def test_with_no_team_connections_the_shared_account_searches_everything():
    r = router()
    await r.refresh(TEAMS)
    assert [(s.name, s.jql) for s in await r.scopes()] == [("(shared)", "")]


async def test_without_a_shared_account_only_connected_teams_are_searched_and_others_fail_clearly():
    r = router(shared=False, teams={"Payments": PAY})
    await r.refresh(TEAMS)
    assert [s.name for s in await r.scopes()] == ["Payments"]
    with pytest.raises(NoConnection, match="MOB"):
        r.for_key("MOB-1")


async def test_the_bot_account_is_looked_up_per_account_and_the_configured_one_wins_for_the_shared_account():
    r = router(teams={"Payments": PAY})
    await r.refresh(TEAMS)
    shared = next(s for s in await r.scopes("configured-bot") if s.name == "(shared)")
    assert shared.bot_account_id == "configured-bot"
    team = next(s for s in await r.scopes() if s.name == "Payments")
    assert team.bot_account_id != "configured-bot"


async def test_an_unchanged_connection_keeps_its_client_and_a_rotated_token_gets_a_new_one():
    creds = Creds({"Payments": PAY})
    r = JiraRouter(shared=None, credentials=creds)
    await r.refresh(TEAMS)
    first = r.for_key("PAY-1")
    await r.refresh(TEAMS)
    assert r.for_key("PAY-1") is first
    creds.by_team["Payments"] = JiraCreds(PAY.base_url, PAY.email, "rotated")
    await r.refresh(TEAMS)
    assert r.for_key("PAY-1") is not first and len(r.clients) == 1  # the old client is dropped


async def test_when_the_portal_cannot_answer_the_last_routing_stays_and_a_first_failure_is_raised():
    creds = Creds({"Payments": PAY})
    r = JiraRouter(shared=Recorder("shared"), credentials=creds)
    creds.error = ConnectionsUnavailable("down")
    with pytest.raises(ConnectionsUnavailable):
        await r.refresh(TEAMS)  # nothing known yet: do not guess
    creds.error = None
    await r.refresh(TEAMS)
    team_client = r.for_key("PAY-1")
    creds.error = ConnectionsUnavailable("down")
    await r.refresh(TEAMS)
    assert r.for_key("PAY-1") is team_client  # never falls back to the shared account for a connected team


async def test_activities_write_through_the_account_that_owns_the_project():
    r = router(teams={"Payments": PAY})
    await r.refresh(TEAMS)
    own = r.for_key("PAY-1")
    recorder = Recorder("pay")
    r.by_project["PAY"] = ("Payments", recorder)
    activities.set_client(r)
    await activities.jira_comment("PAY-5", "hello")
    await activities.jira_set_labels("MOB-5", ["a"], [])
    assert recorder.calls == [("comment", "PAY-5")] and r.shared.calls == [("labels", "MOB-5")] and own is not recorder


async def test_a_project_nobody_covers_is_a_permanent_activity_error():
    from temporalio.exceptions import ApplicationError

    r = router(shared=False)
    await r.refresh(TEAMS)
    activities.set_client(r)
    with pytest.raises(ApplicationError) as e:
        await activities.jira_comment("ZZZ-1", "x")
    assert e.value.non_retryable


async def test_dry_run_wraps_every_team_client_too():
    wrapped = []
    r = router(teams={"Payments": PAY}, wrap=lambda c: wrapped.append(c) or c)
    await r.refresh(TEAMS)
    assert len(wrapped) == 1 and wrapped[0] is r.for_key("PAY-1")


async def test_pickup_and_reply_searches_carry_the_scope():
    from jira_connector.poller import Config, Seen, pickup_cycle, reply_cycle
    from sif_contracts import Settings

    seen = []

    class Searcher(Recorder):
        async def search(self, jql, fields, limit=100):
            seen.append(jql)
            return []

    cfg = Config(settings=Settings(_env_file=None))
    await pickup_cycle(Searcher("x"), None, [], cfg, scope='project in ("PAY")')
    await reply_cycle(Searcher("x"), None, None, [], cfg, Seen(), scope='project not in ("PAY")')
    await pickup_cycle(Searcher("x"), None, [], cfg)
    assert seen[0].endswith('AND project in ("PAY") ORDER BY created ASC') and 'labels != "sif-picked"' in seen[0]
    assert seen[1].endswith('updated >= -2d AND project not in ("PAY")')
    assert "project" not in seen[2]
