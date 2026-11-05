import asyncio

import pytest

from sis_contracts.namespaces import DEFAULT, NamespaceWorkers, check_namespace, namespace_for, namespaces_of


@pytest.mark.parametrize("team, ns", [("Payments", "payments"), ("Mobile Apps", "mobile-apps"), ("  R&D / UK ", "r-d-uk"), ("!!!", "")])
def test_a_team_namespace_is_its_name_in_lowercase_with_dashes(team, ns):
    assert namespace_for(team) == ns


@pytest.mark.parametrize("name", ["payments", "team-2", "a", "default", "x" * 63])
def test_good_namespace_names(name):
    assert check_namespace(name) is None


@pytest.mark.parametrize("name", ["", "Payments", "-lead", "trail-", "a.b", "a_b", "x" * 64, "temporal-system", "../etc"])
def test_bad_namespace_names(name):
    assert check_namespace(name)


def test_every_team_namespace_plus_default():
    teams = [{"temporal_namespace": "payments"}, {"temporal_namespace": "payments"}, {"temporal_namespace": None}, {}]
    assert namespaces_of(teams) == {DEFAULT, "payments"}


class FakeNamespaces:
    def __init__(self, broken=()):
        self.broken, self.ensured = set(broken), []

    async def ready(self, ns):
        if ns in self.broken:
            raise RuntimeError(f"Namespace {ns} is not found")
        self.ensured.append(ns)
        return ns  # the "client" is just the namespace name


class FakeWorker:
    def __init__(self, ns, log, crash=False):
        self.ns, self.log, self.crash = ns, log, crash

    async def run(self):
        self.log.append(self.ns)
        if self.crash:
            raise RuntimeError("poller died")
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_workers_follow_the_teams_and_retry_namespaces_that_are_not_ready():
    wanted, log, nss = {DEFAULT, "payments"}, [], FakeNamespaces(broken={"mobile"})
    w = NamespaceWorkers(nss, lambda c: FakeWorker(c, log), lambda: _value(set(wanted)))
    assert await w.sync() == [DEFAULT, "payments"]
    assert await w.sync() == []  # already running

    wanted.add("mobile")  # a team created on the dashboard, its namespace not usable yet
    assert await w.sync() == [] and "mobile" in w.errors and w.status()["mobile"].startswith("error")
    nss.broken.clear()
    assert await w.sync() == ["mobile"] and "mobile" not in w.errors

    wanted.discard("payments")  # a deleted team: its worker keeps serving workflows already running there
    await w.sync()
    await asyncio.sleep(0)
    assert sorted(set(log)) == [DEFAULT, "mobile", "payments"] and w.status()["payments"] == "running" and w.healthy()
    await w.stop()


@pytest.mark.asyncio
async def test_a_crashed_worker_is_started_again():
    log, crash = [], {"payments": True}

    def make(c):
        worker = FakeWorker(c, log, crash=crash.get(c, False))
        crash[c] = False
        return worker

    w = NamespaceWorkers(FakeNamespaces(), make, lambda: _value({DEFAULT, "payments"}))
    await w.sync()
    await asyncio.sleep(0.01)
    assert w.status()["payments"].startswith("error") or w.status()["payments"] == "stopped"
    assert await w.sync() == ["payments"]
    await asyncio.sleep(0)
    assert log.count("payments") == 2 and w.status()["payments"] == "running"
    await w.stop()


@pytest.mark.asyncio
async def test_portal_down_still_serves_default():
    w = NamespaceWorkers(FakeNamespaces(), lambda c: FakeWorker(c, []), lambda: _value(None))
    assert await w.sync() == [DEFAULT]
    await w.stop()


async def _value(v):
    return v


@pytest.mark.asyncio
@pytest.mark.skipif(not __import__("os").environ.get("TEMPORAL_ADDRESS"), reason="needs TEMPORAL_ADDRESS")
async def test_a_namespace_is_registered_once_on_a_real_server():
    import os
    import uuid

    from sis_contracts.namespaces import Namespaces

    name = f"t-{uuid.uuid4().hex[:12]}"
    first, second = Namespaces(os.environ["TEMPORAL_ADDRESS"]), Namespaces(os.environ["TEMPORAL_ADDRESS"])
    assert await first.ensure(name) is True
    assert await second.ensure(name) is False  # another service finding it already there
    client = await first.client(name)
    assert client.namespace == name and (await first.client(name)) is client
