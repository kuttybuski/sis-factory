"""Teams are Temporal namespaces: each team's tickets run as workflows in its own namespace, named after the team.

namespace_for, check_namespace and namespaces_of are pure. The rest uses temporalio, imported lazily so services without it can still import this module.
- Namespaces: one connection to Temporal, one client per namespace on top of it.
- ensure: registers a namespace if it does not exist yet (portal-api does this when a team is saved; the workers do it too, so a team
  inserted straight into the database, or a reset Temporal, still works).
- NamespaceWorkers: keeps one worker per team namespace running and starts new ones as teams are added, without a restart. A worker is
  never stopped when its team goes away: workflows already running there still need it.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("sif.namespaces")

DEFAULT = "default"
_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def namespace_for(team: str) -> str:
    """A team's namespace is its name in lowercase with dashes: "Mobile Apps" -> "mobile-apps"."""
    return re.sub(r"[^a-z0-9]+", "-", team.lower()).strip("-")[:63].strip("-")


def check_namespace(name: str) -> str | None:
    """Why this cannot be a namespace name, or None. Stricter than Temporal (lowercase, digits, dashes) so it is also safe in URLs and labels."""
    if not _NAME.match(name or ""):
        return "namespace must be 1-63 characters: lowercase letters, digits and dashes, starting and ending with a letter or digit"
    if name.startswith("temporal-"):
        return "namespaces starting with temporal- are reserved by Temporal"
    return None


def namespaces_of(teams: list[dict[str, Any]]) -> set[str]:
    """Every namespace a worker must serve: the teams' plus default (tickets that match no team)."""
    return {DEFAULT} | {t.get("temporal_namespace") or DEFAULT for t in teams}


class Namespaces:
    """Clients for any namespace over a single Temporal connection. Namespaces are registered on first use."""

    def __init__(self, address: str, retention_days: int = 30, tries: int = 60) -> None:
        self.address, self.retention_days, self.tries = address, retention_days, tries
        self._base: Any = None
        self._clients: dict[str, Any] = {}
        self._ensured: set[str] = set()

    async def _service(self) -> Any:
        """The one connection, retried while Temporal starts (services usually come up before it)."""
        if self._base is None:
            from temporalio.client import Client

            for attempt in range(self.tries):
                try:
                    self._base = await Client.connect(self.address, namespace=DEFAULT)
                    break
                except Exception:
                    if attempt == self.tries - 1:
                        raise
                    await asyncio.sleep(2)
        return self._base

    async def client(self, namespace: str = DEFAULT) -> Any:
        if namespace not in self._clients:
            base = await self._service()
            if namespace == getattr(base, "namespace", DEFAULT):
                self._clients[namespace] = base
            else:
                from temporalio.client import Client

                self._clients[namespace] = Client(base.service_client, namespace=namespace, data_converter=base.data_converter)
        return self._clients[namespace]

    async def ensure(self, namespace: str) -> bool:
        """Registers the namespace unless it exists. True when it was created now."""
        if namespace in self._ensured:
            return False
        created = await register(await self._service(), namespace, self.retention_days)
        self._ensured.add(namespace)
        return created

    async def ready(self, namespace: str) -> Any:
        await self.ensure(namespace)
        return await self.client(namespace)


async def register(base: Any, namespace: str, retention_days: int = 30) -> bool:
    """Creates a namespace on the Temporal server. True if created, False if it already existed."""
    from google.protobuf.duration_pb2 import Duration
    from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest, RegisterNamespaceRequest
    from temporalio.service import RPCError, RPCStatusCode

    svc = base.workflow_service
    try:
        await svc.describe_namespace(DescribeNamespaceRequest(namespace=namespace))
        return False
    except RPCError as e:
        if e.status != RPCStatusCode.NOT_FOUND:
            raise
    try:
        await svc.register_namespace(RegisterNamespaceRequest(
            namespace=namespace, description="SIF team namespace", workflow_execution_retention_period=Duration(seconds=retention_days * 86400)))
        log.info("registered Temporal namespace %s", namespace)
        return True
    except RPCError as e:
        if e.status == RPCStatusCode.ALREADY_EXISTS:
            return False
        raise


class NamespaceWorkers:
    """One worker per namespace, kept in step with the list of teams."""

    def __init__(self, namespaces: Namespaces, make_worker: Callable[[Any], Any], wanted: Callable[[], Awaitable[set[str] | None]], every: float = 15) -> None:
        self.namespaces, self.make_worker, self.wanted, self.every = namespaces, make_worker, wanted, every
        self.running: dict[str, asyncio.Task] = {}
        self.errors: dict[str, str] = {}

    async def sync(self) -> list[str]:
        """Starts workers for namespaces that have none (or whose worker stopped). Returns the namespaces started."""
        names = await self.wanted()
        if names is None:  # portal-api unreachable: keep what runs, at least serve default
            names = {DEFAULT}
        started = []
        for ns in sorted(names):
            task = self.running.get(ns)
            if task is not None and not task.done():
                continue
            if task is not None and task.done() and not task.cancelled() and task.exception():
                self.errors[ns] = f"{type(task.exception()).__name__}: {task.exception()}"
                log.warning("worker in namespace %s stopped (%s); starting it again", ns, self.errors[ns])
            try:
                client = await self.namespaces.ready(ns)
                self.running[ns] = asyncio.create_task(self.make_worker(client).run(), name=f"worker:{ns}")
                self.errors.pop(ns, None)
                started.append(ns)
            except Exception as e:  # a namespace just registered can take a few seconds to become usable; the next sync retries
                self.errors[ns] = f"{type(e).__name__}: {e}"
                log.warning("cannot start a worker in namespace %s yet: %s", ns, e)
        return started

    async def run(self) -> None:
        while True:
            try:
                await self.sync()
            except Exception as e:
                log.warning("namespace sync failed: %s", e)
            await asyncio.sleep(self.every)

    def status(self) -> dict[str, str]:
        out = {ns: ("running" if not t.done() else "stopped") for ns, t in self.running.items()}
        out.update({ns: f"error: {msg}" for ns, msg in self.errors.items() if ns not in out or out[ns] != "running"})
        return out

    def healthy(self) -> bool:
        t = self.running.get(DEFAULT)
        return t is not None and not t.done()

    async def stop(self) -> None:
        for t in self.running.values():
            t.cancel()
        await asyncio.gather(*self.running.values(), return_exceptions=True)


async def team_namespaces(portal: Any) -> set[str] | None:
    """The namespaces of every team, from portal-api's public team list. None if the portal cannot be reached."""
    try:
        r = await portal.get("/api/teams")
        r.raise_for_status()
        return namespaces_of(r.json())
    except Exception as e:
        log.warning("cannot list teams: %s", e)
        return None
