"""Which Jira account acts for which project.

A team can have its own Jira connection (saved on the dashboard, fetched from portal-api). Its projects are read and written with that account. Every other
project uses the shared account from the environment, if there is one. The router looks like a single JiraClient to the Temporal activities, which only
know an issue key: the project in front of the dash picks the account."""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sif_contracts.connections import ConnectionsUnavailable, Credentials, JiraCreds

from jira_connector.client import JiraClient

log = logging.getLogger("jira_connector")


class NoConnection(Exception):
    """No team connection and no shared account covers this project."""


@dataclass
class Scope:
    name: str  # team name, or "(shared)"
    client: JiraClient
    jql: str  # narrows a search to this account's projects; empty = everything
    bot_account_id: str = ""


@dataclass
class JiraRouter:
    shared: JiraClient | None = None
    credentials: Credentials | None = None
    wrap: Callable[[JiraClient], JiraClient] = lambda c: c  # dry-run mode wraps every client so nothing writes
    clients: dict[str, JiraClient] = field(default_factory=dict)  # credential fingerprint -> client, so an unchanged connection keeps its HTTP session
    by_project: dict[str, tuple[str, JiraClient]] = field(default_factory=dict)  # project -> (team, client)
    shared_bot: str = ""
    status: dict[str, str] = field(default_factory=dict)
    bots: dict[int, str] = field(default_factory=dict)  # id(client) -> its account id

    # ------------------------------------------------------------------ routing
    async def refresh(self, teams: list[dict]) -> None:
        """Rebuilds the project -> account map. If the portal cannot answer, the last map stays in force; with no last map the error is raised."""
        by_project: dict[str, tuple[str, JiraClient]] = {}
        status: dict[str, str] = {}
        used: dict[str, JiraClient] = {}
        if self.credentials is not None:
            for t in teams:
                projects = t.get("jira_projects") or []
                if not projects:
                    continue
                try:
                    creds = await self.credentials.jira(t["name"])
                except ConnectionsUnavailable as e:
                    if self.by_project or self.clients:
                        log.warning("keeping the previous Jira routing: %s", e)
                        return
                    raise
                if creds is None:
                    status[t["name"]] = "shared"
                    continue
                client = self._client_for(creds, used)
                status[t["name"]] = "own connection"
                for p in projects:
                    by_project[p] = (t["name"], client)
        self.by_project, self.status, self.clients = by_project, status, used

    def _client_for(self, creds: JiraCreds, used: dict[str, JiraClient]) -> JiraClient:
        fingerprint = hashlib.sha256(f"{creds.base_url}\0{creds.email}\0{creds.token}".encode()).hexdigest()
        if fingerprint not in used:
            used[fingerprint] = self.clients.get(fingerprint) or self.wrap(JiraClient(creds.base_url, creds.email, creds.token))
        return used[fingerprint]

    def for_project(self, project: str) -> JiraClient:
        if project in self.by_project:
            return self.by_project[project][1]
        if self.shared is not None:
            return self.shared
        raise NoConnection(f"no Jira connection covers project {project}: connect the team on the Connections page or set the shared JIRA_* credentials")

    def for_key(self, key: str) -> JiraClient:
        return self.for_project(key.split("-")[0])

    # ------------------------------------------------------------------ what the poller searches
    async def scopes(self, configured_bot_id: str = "") -> list[Scope]:
        """One search scope per account: each team connection for its own projects, the shared account for all the rest."""
        out: list[Scope] = []
        by_client: dict[int, tuple[str, JiraClient, list[str]]] = {}
        for project, (team, client) in self.by_project.items():
            by_client.setdefault(id(client), (team, client, []))[2].append(project)
        for team, client, projects in by_client.values():
            out.append(Scope(team, client, "project in (" + ", ".join(f'"{p}"' for p in sorted(projects)) + ")", await self._bot(client)))
        if self.shared is not None:
            owned = sorted(self.by_project)
            jql = "project not in (" + ", ".join(f'"{p}"' for p in owned) + ")" if owned else ""
            if not self.shared_bot:
                self.shared_bot = configured_bot_id or await self._bot(self.shared)
            out.append(Scope("(shared)", self.shared, jql, self.shared_bot))
        return out

    async def _bot(self, client: JiraClient) -> str:
        if id(client) not in self.bots:
            try:
                self.bots[id(client)] = (await client.myself())["accountId"]  # our own comments must not be treated as replies
            except Exception as e:
                log.warning("could not look up the bot account (%s); its own comments may be read as replies", type(e).__name__)
                return ""
        return self.bots[id(client)]

    # ------------------------------------------------------------------ the JiraClient surface the activities use
    async def add_comment(self, key: str, text: str) -> str:
        return await self.for_key(key).add_comment(key, text)

    async def update_labels(self, key: str, add: list[str], remove: list[str]) -> None:
        await self.for_key(key).update_labels(key, add, remove)

    async def create_issue(self, project: str, summary: str, description: str, issue_type: str, parent_key: str | None = None, labels: list[str] | None = None) -> str:
        return await self.for_project(project).create_issue(project, summary, description, issue_type, parent_key, labels)
