import json

import httpx
import pytest
from jira_connector import activities
from jira_connector.adf import adf_to_text, clean, text_to_adf
from jira_connector.client import DryRunJira, JiraClient
from jira_connector.poller import Config, Seen, build_input, pickup_cycle, relay_answer, repo_for, reply_cycle, resolve_role
from sis_contracts import Settings
from sis_contracts import lessons as L
from sis_contracts.workflow import SIG_REPLY

DESC = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Retries use a fixed delay. It should use backoff."}]},
                                                  {"type": "paragraph", "content": [{"type": "text", "text": "Repo: acme/api"}]}]}


# ---- ADF
def test_adf_to_text_handles_mentions_breaks_and_code():
    doc = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "mention", "attrs": {"id": "abc123", "text": "@sis"}}, {"type": "text", "text": " use backoff"}, {"type": "hardBreak"}, {"type": "text", "text": "thanks"}]},
        {"type": "codeBlock", "content": [{"type": "text", "text": "x = 1"}]}]}
    out = clean(adf_to_text(doc))
    assert out.startswith("[~accountid:abc123] use backoff\nthanks") and "```\nx = 1\n```" in out


def test_text_to_adf_makes_paragraphs_and_breaks():
    doc = text_to_adf("line one\nline two\n\nsecond paragraph")
    assert [p["type"] for p in doc["content"]] == ["paragraph", "paragraph"]
    assert [n["type"] for n in doc["content"][0]["content"]] == ["text", "hardBreak", "text"]
    assert clean(adf_to_text(doc)) == "line one\nline two\n\nsecond paragraph"
    assert text_to_adf("")["content"]  # never an empty document: Jira rejects it


# ---- client
@pytest.mark.asyncio
async def test_search_follows_the_page_token_and_comments_are_converted():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.url.path == "/rest/api/3/search/jql":
            body = json.loads(req.content)
            page2 = body.get("nextPageToken") == "t2"
            return httpx.Response(200, json={"issues": [{"key": "PAY-2" if page2 else "PAY-1"}], **({} if page2 else {"nextPageToken": "t2"})})
        return httpx.Response(200, json={"total": 2, "comments": [{"id": 1, "author": {"accountId": "a"}, "body": DESC, "created": "x"}, {"id": 2, "author": {}, "body": None}]})

    c = JiraClient(client=httpx.AsyncClient(base_url="https://j", transport=httpx.MockTransport(handler)))
    assert [i["key"] for i in await c.search("labels = sis", ["summary"])] == ["PAY-1", "PAY-2"]
    comments = await c.comments("PAY-1")
    assert [x["id"] for x in comments] == ["1", "2"] and "Retries use a fixed delay" in comments[0]["text"]


@pytest.mark.asyncio
async def test_writes_use_adf_and_label_operations():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        return httpx.Response(201 if req.method == "POST" else 204, json={"id": "10001"} if req.method == "POST" else {})

    c = JiraClient(client=httpx.AsyncClient(base_url="https://j", transport=httpx.MockTransport(handler)))
    assert await c.add_comment("PAY-1", "hello") == "10001"  # the new comment's id
    await c.update_labels("PAY-1", ["sis-picked"], ["sis"])
    await c.update_labels("PAY-1", [], [])  # nothing to do: no call
    assert calls[0][2]["body"]["type"] == "doc" and len(calls) == 2
    assert calls[1] == ("PUT", "/rest/api/3/issue/PAY-1", {"update": {"labels": [{"remove": "sis"}, {"add": "sis-picked"}]}})


@pytest.mark.asyncio
async def test_dry_run_reads_but_never_writes():
    logged = []
    inner = JiraClient(client=httpx.AsyncClient(base_url="https://j", transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    d = DryRunJira(inner, lambda *a: logged.append(a[0] % a[1:]))
    await d.add_comment("PAY-1", "hi")
    await d.update_labels("PAY-1", ["a"], ["b"])
    assert len(logged) == 2 and all(l.startswith("DRY RUN") for l in logged)


@pytest.mark.asyncio
async def test_activities_distinguish_retryable_from_permanent_errors():
    from temporalio.exceptions import ApplicationError

    for status, permanent in ((403, True), (404, True), (429, False), (503, False)):
        activities.set_client(JiraClient(client=httpx.AsyncClient(base_url="https://j", transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s, json={})))))
        with pytest.raises(Exception) as e:
            await activities.jira_comment("PAY-1", "x")
        assert isinstance(e.value, ApplicationError) == permanent


@pytest.mark.asyncio
async def test_create_issue_sends_parent_labels_and_adf_and_returns_the_key():
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append((req.method, req.url.path, json.loads(req.content)))
        return httpx.Response(201, json={"key": "PAY-7"})

    c = JiraClient(client=httpx.AsyncClient(base_url="https://j", transport=httpx.MockTransport(handler)))
    assert await c.create_issue("PAY", "Add backoff", "Do it.\nRepo: acme/api", "Story", "PAY-1", ["sis"]) == "PAY-7"
    f = sent[0][2]["fields"]
    assert sent[0][:2] == ("POST", "/rest/api/3/issue")
    assert (f["project"], f["issuetype"], f["parent"], f["labels"], f["description"]["type"]) == ({"key": "PAY"}, {"name": "Story"}, {"key": "PAY-1"}, ["sis"], "doc")
    await c.create_issue("PAY", "No parent", "x", "Task", None, [])
    assert "parent" not in sent[1][2]["fields"]


@pytest.mark.asyncio
async def test_dry_run_creates_nothing_and_the_create_activity_is_permanent_on_4xx():
    from temporalio.exceptions import ApplicationError

    logged = []
    d = DryRunJira(JiraClient(client=httpx.AsyncClient(base_url="https://j", transport=httpx.MockTransport(lambda r: httpx.Response(500)))), lambda *a: logged.append(a[0] % a[1:]))
    assert await d.create_issue("PAY", "s", "d", "Story", "PAY-1", ["sis"]) == "PAY-DRY" and logged[0].startswith("DRY RUN")
    activities.set_client(JiraClient(client=httpx.AsyncClient(base_url="https://j", transport=httpx.MockTransport(lambda r: httpx.Response(400, json={})))))
    with pytest.raises(ApplicationError):
        await activities.jira_create_issue("PAY", "s", "d", "story", "PAY-1")


# ---- fakes for the poller
class FakeJira(JiraClient):
    def __init__(self, issues, comments=None):
        self.issues, self.by_key, self.written, self.labels_changed = issues, comments or {}, [], []

    async def search(self, jql, fields, limit=100):
        return self.issues

    async def comments(self, key):
        return self.by_key.get(key, [])

    async def add_comment(self, key, text):
        self.written.append((key, text))
        return f"c{len(self.written)}"

    async def update_labels(self, key, add, remove):
        self.labels_changed.append((key, add, remove))


class FakeFlow:
    def __init__(self, running=0, live=("PAY-1",)):
        self.started, self.signals, self._running, self.live = [], [], running, set(live)

    async def start(self, key, inp):
        self.started.append((key, inp))
        return True

    async def signal(self, key, name, arg, namespace="default"):
        if key not in self.live:
            return False
        self.signals.append((key, name, arg))
        self.namespaces = [*getattr(self, "namespaces", []), namespace]
        return True

    async def running(self):
        return self._running


def issue(key="PAY-1", labels=("sis",), desc=DESC, reporter="r1", **extra):
    return {"key": key, "fields": {"summary": "Add retry", "description": desc, "labels": list(labels), "reporter": {"accountId": reporter, "displayName": "Rae"}, **extra}}


TEAMS = [{"name": "Payments", "jira_projects": ["PAY"], "temporal_namespace": "payments", "repos": ["acme/api"]}]


def cfg(**kw):
    return Config(settings=Settings(_env_file=None), **kw)


# ---- input building
def test_repo_comes_from_the_ticket_or_the_teams_only_repo():
    assert repo_for("text\nRepo: acme/web\nmore", {"repos": ["acme/api"]}) == "acme/web"
    assert repo_for("no repo here", {"repos": ["acme/api"]}) == "acme/api"
    assert repo_for("no repo here", {"repos": ["acme/api", "acme/web"]}) is None and repo_for("x", None) is None


def test_with_several_team_repos_the_component_or_a_mention_picks_one():
    team = {"repos": ["acme/api", "acme/web", "acme/web-admin"]}
    assert repo_for("Fix login", team, components=["web"]) == "acme/web"
    assert repo_for("Fix login", team, components=["acme/api"]) == "acme/api"
    assert repo_for("The api returns 500 on refunds", team) == "acme/api"
    assert repo_for("Broken button", team, summary="web-admin: save fails") == "acme/web-admin"  # web-admin, not web
    assert repo_for("Both web and api need it", team) is None  # ambiguous: ask instead of guessing


def test_build_input_carries_team_budget_and_limits():
    inp = build_input(issue(), TEAMS[0], cfg(wait_timeout_seconds=600))
    assert (inp.jira_key, inp.team, inp.namespace, inp.repo, inp.budget_usd, inp.wait_timeout_seconds, inp.mention) == ("PAY-1", "Payments", "payments", "acme/api", 2.0, 600, "@sis")
    assert "Repo: acme/api" in inp.description
    assert "Repo: owner/name" in build_input(issue(desc=None), {"repos": []}, cfg())


# ---- pickup
@pytest.mark.asyncio
async def test_a_labelled_ticket_starts_a_workflow_then_swaps_the_label():
    jira, flow = FakeJira([issue()]), FakeFlow()
    assert await pickup_cycle(jira, flow, TEAMS, cfg()) == ["PAY-1"]
    assert flow.started[0][0] == "PAY-1" and flow.started[0][1].namespace == "payments"
    assert jira.labels_changed == [("PAY-1", ["sis-picked"], ["sis"])]


@pytest.mark.asyncio
async def test_tickets_already_picked_are_skipped():
    jira, flow = FakeJira([issue(labels=("sis", "sis-picked"))]), FakeFlow()
    assert await pickup_cycle(jira, flow, TEAMS, cfg()) == [] and flow.started == []


@pytest.mark.asyncio
async def test_a_ticket_without_a_repo_gets_a_helpful_comment_instead():
    jira, flow = FakeJira([issue(desc=None)]), FakeFlow()
    teams = [{"name": "Payments", "jira_projects": ["PAY"], "repos": ["a/b", "c/d"]}]
    assert await pickup_cycle(jira, flow, teams, cfg()) == [] and flow.started == []
    assert "Repo: owner/name" in jira.written[0][1] and jira.labels_changed == [("PAY-1", ["sis-needs-info"], ["sis"])]


@pytest.mark.asyncio
async def test_projects_outside_the_allowlist_are_ignored():
    jira, flow = FakeJira([issue("OPS-9"), issue("PAY-1")]), FakeFlow()
    assert await pickup_cycle(jira, flow, TEAMS, cfg(allowed_projects={"PAY"})) == ["PAY-1"]


@pytest.mark.asyncio
async def test_the_concurrency_cap_leaves_extra_tickets_for_later():
    jira = FakeJira([issue("PAY-1"), issue("PAY-2")])
    assert await pickup_cycle(jira, FakeFlow(running=5), TEAMS, cfg(max_concurrent=5)) == []
    assert jira.labels_changed == []  # still labelled, so the next cycle will try again


@pytest.mark.asyncio
async def test_a_paused_intake_leaves_tickets_labelled_for_later():
    jira, flow = FakeJira([issue("PAY-1")]), FakeFlow()
    paused = {"pauses": [{"team": "Payments", "paused_by": "rae@acme.com", "reason": "incident"}], "counts": {}}
    assert await pickup_cycle(jira, flow, TEAMS, cfg(), intake=paused) == [] and jira.labels_changed == []
    everyone = {"pauses": [{"team": "*", "paused_by": "rae@acme.com"}], "counts": {}}
    assert await pickup_cycle(jira, flow, TEAMS, cfg(), intake=everyone) == []
    other = {"pauses": [{"team": "Mobile", "paused_by": "rae@acme.com"}], "counts": {}}
    assert await pickup_cycle(jira, flow, TEAMS, cfg(), intake=other) == ["PAY-1"]


@pytest.mark.asyncio
async def test_per_team_limits_from_portal_api_hold_within_one_cycle():
    from sis_contracts.intake import held_back

    jira, flow = FakeJira([issue("PAY-1"), issue("PAY-2"), issue("PAY-3")]), FakeFlow()
    intake = {"pauses": [], "limits": {"max_running": 0, "max_daily": 10}, "counts": {"Payments": {"running": 0, "started_24h": 8}}}
    assert await pickup_cycle(jira, flow, TEAMS, cfg(), intake=intake) == ["PAY-1", "PAY-2"]
    assert "running task" in held_back("Payments", {"limits": {"max_running": 2}, "counts": {"Payments": {"running": 2}}})
    assert held_back("Payments", {"counts": {"Payments": {"running": 99, "started_24h": 99}}}) is None  # no limits reported, none applied
    assert held_back("Payments", None) is None


@pytest.mark.asyncio
async def test_dry_run_starts_nothing():
    jira, flow = FakeJira([issue()]), FakeFlow()
    assert await pickup_cycle(jira, flow, TEAMS, cfg(), dry_run=True) == [] and flow.started == []


# ---- roles
def test_roles_come_from_the_configured_lists_or_default_to_reporter_and_assignee():
    c = Config.from_env({"SIS_RESPONDERS": "ann@x.com, Bob", "SIS_MAINTAINERS": "acc-m", "SIS_ADMINS": "acc-a"})
    assert c.responders == {"ann@x.com": L.Role.MEMBER, "Bob": L.Role.MEMBER, "acc-m": L.Role.MAINTAINER, "acc-a": L.Role.ADMIN}
    assert resolve_role({"emailAddress": "ann@x.com"}, {}, c) is L.Role.MEMBER and resolve_role({"accountId": "acc-m"}, {}, c) is L.Role.MAINTAINER
    assert resolve_role({"accountId": "stranger"}, {}, c) is L.Role.NONE
    f = {"reporter": {"accountId": "r1"}, "assignee": {"accountId": "a1"}}
    open_cfg = cfg()  # no list configured
    assert resolve_role({"accountId": "r1"}, f, open_cfg) is L.Role.MEMBER and resolve_role({"accountId": "nobody"}, f, open_cfg) is L.Role.NONE
    assert resolve_role({"accountId": "r1"}, f, c) is L.Role.NONE  # once a list exists, only listed people count


# ---- replies and commands
def comment(cid, text, who="r1", name="Rae"):
    return {"id": cid, "text": text, "author": {"accountId": who, "displayName": name}}


class Portal:
    def __init__(self, lessons=None):
        self.posted, self.disabled, self.lessons = [], [], lessons or []

    def client(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "POST" and req.url.path == "/api/lessons":
                body = json.loads(req.content)
                self.posted.append(body)
                if len(body["text"]) < 10:
                    return httpx.Response(422, json={"detail": "too short"})
                status = "approved" if body["author_role"] in ("maintainer", "admin") else "candidate"
                return httpx.Response(201, json={"id": 12, "status": status, "duplicate": False, "reason": None})
            if req.method == "GET":
                return httpx.Response(200, json=self.lessons)
            self.disabled.append((req.url.path, json.loads(req.content)))
            return httpx.Response(200, json={})

        return httpx.AsyncClient(base_url="http://portal", transport=httpx.MockTransport(handler))


async def replies(jira, flow=None, portal=None, config=None, seen=None):
    return await reply_cycle(jira, flow or FakeFlow(), (portal or Portal()).client(), TEAMS, config or cfg(), seen or Seen())


@pytest.mark.asyncio
async def test_an_answer_from_the_3d_view_is_posted_on_jira_first_and_signalled_once():
    jira, flow, seen = FakeJira([issue()]), FakeFlow(), Seen()
    assert await relay_answer(jira, flow, TEAMS[0], "PAY-1", "Use exponential backoff.", "ann@acme.com", seen)
    assert jira.written == [("PAY-1", "ann@acme.com answered in the SIS 3D view:\n\nUse exponential backoff.")]
    key, name, reply = flow.signals[0]
    assert (key, name, reply.text, reply.author, reply.comment_id) == ("PAY-1", SIG_REPLY, "Use exponential backoff.", "ann@acme.com", "PAY-1:c1")
    assert flow.namespaces == ["payments"]
    # the next reply cycle reads that comment back from Jira and does not pass it on again
    jira.by_key["PAY-1"] = [comment("c1", "@sis ann@acme.com answered in the SIS 3D view: Use exponential backoff.")]
    assert (await replies(jira, flow, seen=seen))["replies"] == 0 and len(flow.signals) == 1
    # no running task: the answer is still recorded on Jira, and the caller hears it was not delivered
    assert not await relay_answer(jira, FakeFlow(live=()), None, "PAY-2", "ok", "ann", seen) and jira.written[-1][0] == "PAY-2"


@pytest.mark.asyncio
async def test_an_allowed_reply_is_signalled_with_the_mention_removed():
    jira, flow = FakeJira([issue()], {"PAY-1": [comment("5", "@sis Use exponential backoff, max 5.")]}), FakeFlow()
    assert (await replies(jira, flow))["replies"] == 1
    key, name, reply = flow.signals[0]
    assert (key, name, reply.text, reply.comment_id, reply.author) == ("PAY-1", SIG_REPLY, "Use exponential backoff, max 5.", "PAY-1:5", "Rae")
    assert flow.namespaces == ["payments"]  # the reply goes to the workflow in the team's namespace


@pytest.mark.asyncio
async def test_comments_that_are_not_for_the_agent_or_from_strangers_or_the_bot_are_ignored():
    c = cfg(bot_account_id="bot1")
    comments = [comment("1", "Looks fine to me"), comment("2", "@sis approve", who="stranger", name="Eve"), comment("3", "@sis posting as myself", who="bot1")]
    flow = FakeFlow()
    c.responders = {"someone-else": L.Role.MEMBER}
    out = await replies(FakeJira([issue()], {"PAY-1": comments}), flow, config=c)
    assert flow.signals == [] and out == {"replies": 0, "commands": 0, "ignored": 1}


@pytest.mark.asyncio
async def test_a_real_jira_mention_of_the_bot_counts():
    c = cfg(bot_account_id="bot1")
    flow = FakeFlow()
    await replies(FakeJira([issue()], {"PAY-1": [comment("1", "[~accountid:bot1] go with backoff")]}), flow, config=c)
    assert flow.signals[0][2].text == "go with backoff"


@pytest.mark.asyncio
async def test_each_comment_is_handled_once():
    jira, flow, seen = FakeJira([issue()], {"PAY-1": [comment("5", "@sis approve")]}), FakeFlow(), Seen()
    await replies(jira, flow, seen=seen)
    await replies(jira, flow, seen=seen)
    assert len(flow.signals) == 1


@pytest.mark.asyncio
async def test_replies_to_tickets_without_a_running_workflow_are_dropped():
    flow = FakeFlow(live=())
    assert (await replies(FakeJira([issue()], {"PAY-1": [comment("5", "@sis approve")]}), flow))["replies"] == 0


@pytest.mark.asyncio
async def test_remember_commands_reach_the_lesson_store_and_get_an_answer_in_jira():
    portal, jira, flow = Portal(), FakeJira([issue()], {"PAY-1": [comment("7", "@sis remember always run the migration linter before a PR")]}), FakeFlow()
    c = cfg()
    c.responders = {"r1": L.Role.MAINTAINER}
    out = await replies(jira, flow, portal, c)
    assert out["commands"] == 1 and flow.signals == []  # a command is not an answer to a question
    sent = portal.posted[0]
    assert (sent["scope"], sent["repo"], sent["author_role"], sent["ticket_key"]) == ("repo", "acme/api", "maintainer", "PAY-1")
    assert "L12" in jira.written[0][1] and "active" in jira.written[0][1]


@pytest.mark.asyncio
async def test_members_get_a_queued_lesson_and_scope_options_are_honoured():
    portal, jira = Portal(), FakeJira([issue()], {"PAY-1": [comment("7", "@sis remember keep PRs under 300 lines scope=team")]})
    await replies(jira, portal=portal)
    assert portal.posted[0]["scope"] == "team" and portal.posted[0]["scope_value"] == "Payments" and "approves it" in jira.written[0][1]


@pytest.mark.asyncio
async def test_a_too_short_lesson_is_explained_not_saved():
    portal, jira = Portal(), FakeJira([issue()], {"PAY-1": [comment("7", "@sis remember x")]})
    await replies(jira, portal=portal)
    assert portal.posted == [] and "at least 10 characters" in jira.written[0][1]


@pytest.mark.asyncio
async def test_forget_checks_who_may_remove_a_lesson():
    lessons = [{"id": 3, "author": "Rae", "text": "x"}, {"id": 4, "author": "Someone Else", "text": "y"}]
    portal = Portal(lessons)
    jira = FakeJira([issue()], {"PAY-1": [comment("1", "@sis forget L3"), comment("2", "@sis forget L4"), comment("3", "@sis forget L99")]})
    await replies(jira, portal=portal)
    assert [p for p, _ in portal.disabled] == ["/api/lessons/3/disable"]  # own lesson only; L4 belongs to someone else, L99 does not exist
    assert "removed" in jira.written[0][1] and "only the author or a maintainer" in jira.written[1][1] and "cannot find" in jira.written[2][1]


# ---- handling kinds and policy snapshots
def typed(name, **kw):
    return issue(issuetype={"name": name}, **kw)


def test_the_jira_issue_type_decides_the_kind_and_the_parent_is_carried():
    assert build_input(typed("Epic"), TEAMS[0], cfg()).kind == "epic"
    assert build_input(typed("Bug"), TEAMS[0], cfg()).kind == "task"
    assert build_input(issue(), TEAMS[0], cfg()).kind == "task"  # no issuetype field at all
    inp = build_input(typed("Story", parent={"key": "PAY-1"}), TEAMS[0], cfg())
    assert (inp.kind, inp.parent_key) == ("story", "PAY-1")


def test_without_a_fetched_policy_the_built_in_policy_for_the_kind_is_used():
    epic = build_input(typed("Epic"), TEAMS[0], cfg()).policy
    assert (epic["split_into"], epic["split_approval"], epic["implement"]) == ("story", True, False)
    story = build_input(typed("Story"), TEAMS[0], cfg()).policy
    assert (story["intent"], story["design"], story["implement"]) == (False, False, True)
    custom = {"intent": True, "implement": True, "instructions": "x"}
    assert build_input(typed("Story"), TEAMS[0], cfg(), custom).policy == custom


@pytest.mark.asyncio
async def test_pickup_asks_for_the_policy_of_the_tickets_team_and_kind():
    asked = []

    async def policy_for(team, kind):
        asked.append((team, kind))
        return {"intent": True, "intent_approval": True, "implement": True}

    flow = FakeFlow()
    assert await pickup_cycle(FakeJira([typed("Story")]), flow, TEAMS, cfg(), policy_for=policy_for) == ["PAY-1"]
    assert asked == [("Payments", "story")] and flow.started[0][1].policy["intent_approval"] is True


@pytest.mark.asyncio
async def test_a_portal_that_cannot_answer_does_not_hold_tickets_back():
    from jira_connector.main import effective_policy

    class Down:
        async def get(self, *a, **k):
            raise httpx.ConnectError("nope")

    assert await effective_policy(Down(), "Payments", "story") is None

    class Ok:
        async def get(self, path, params=None):
            return httpx.Response(200, json={"policy": {"intent": True}}, request=httpx.Request("GET", "http://x"))

    assert await effective_policy(Ok(), None, "story") == {"intent": True}
