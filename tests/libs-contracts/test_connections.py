import httpx
import pytest

from sif_contracts import connections as C


def public(host):
    return ["93.184.216.34"]


@pytest.mark.parametrize("url,fragment", [
    ("not a url", "full URL"), ("ftp://x.example", "full URL"), ("https://user:pw@x.example", "credentials"), ("http://x.example", "must use https"),
])
def test_bad_urls_are_explained(url, fragment):
    assert fragment in C.check_url(url, resolver=public)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "192.168.0.9", "169.254.169.254", "::1", "172.16.5.5"])
def test_internal_addresses_are_refused_unless_allowed(address):
    assert "private address" in C.check_url("https://jira.example.com", resolver=lambda h: [address])
    assert C.check_url("http://mock:8080", allow_private=True) is None


def test_a_public_https_url_is_fine_and_unresolvable_hosts_are_reported():
    assert C.check_url("https://acme.atlassian.net", resolver=public) is None

    def broken(host):
        raise OSError("nope")

    assert "cannot resolve" in C.check_url("https://nowhere.example", resolver=broken)


def test_urls_are_normalised_and_secrets_redacted():
    assert C.normalise_url(" https://a.example/ ") == "https://a.example"
    assert C.redact("bad token tok123 for tok123", "tok123", "") == "bad token *** for ***"


class Portal:
    def __init__(self):
        self.calls, self.status, self.body = 0, 200, {"config": {"base_url": "https://a.example", "email": "bot@a"}, "secret": "tok"}

    def transport(self):
        def handler(req: httpx.Request) -> httpx.Response:
            self.calls += 1
            assert req.headers["authorization"] == "Bearer internal"
            if self.status == "down":
                raise httpx.ConnectError("down")
            return httpx.Response(self.status, json=self.body)
        return httpx.MockTransport(handler)


def make(portal, ttl=60.0, now=[0.0]):
    client = httpx.AsyncClient(base_url="http://portal", headers={"Authorization": "Bearer internal"}, transport=portal.transport())
    return C.Credentials("", "", ttl=ttl, client=client, clock=lambda: now[0]), now


async def test_credentials_are_fetched_cached_and_refreshed_after_the_ttl():
    p = Portal()
    creds, now = make(p)
    assert await creds.jira("Payments") == C.JiraCreds("https://a.example", "bot@a", "tok")
    await creds.jira("Payments")
    assert p.calls == 1
    p.body = {"config": {"base_url": "https://a.example", "email": "bot@a"}, "secret": "rotated"}
    now[0] = 61
    assert (await creds.jira("Payments")).token == "rotated" and p.calls == 2


async def test_a_team_without_a_connection_gets_none_and_that_is_cached_too():
    p = Portal()
    p.status = 404
    creds, _ = make(p)
    assert await creds.jira("Mobile") is None and await creds.github("Mobile") is None
    assert await creds.jira("Mobile") is None and p.calls == 2  # one lookup per provider, not per call


async def test_github_defaults_the_urls():
    p = Portal()
    p.body = {"config": {}, "secret": "ghp"}
    creds, _ = make(p)
    assert await creds.github("Payments") == C.GithubCreds("ghp", C.DEFAULT_GITHUB_API, C.DEFAULT_GITHUB_GIT)


async def test_an_unreachable_portal_never_falls_back_to_the_shared_account():
    p = Portal()
    p.status = "down"
    creds, now = make(p)
    with pytest.raises(C.ConnectionsUnavailable):
        await creds.jira("Payments")
    p.status = 200
    await creds.jira("Payments")
    p.status, now[0] = "down", 500
    assert (await creds.jira("Payments")).token == "tok"  # stale credentials beat guessing a different identity
    p.status = 500
    now[0] = 1000
    assert (await creds.jira("Payments")).token == "tok"


async def test_an_error_status_without_a_cache_is_unavailable_not_none():
    p = Portal()
    p.status = 500
    creds, _ = make(p)
    with pytest.raises(C.ConnectionsUnavailable):
        await creds.github("Payments")
