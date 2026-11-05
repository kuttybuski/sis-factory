"""Per-team connections to Jira and GitHub: what they hold, which URLs are acceptable, and how a service fetches a team's credentials.

portal-api is the vault. It encrypts the secrets and hands them, decrypted, only to the services that use them (jira-connector, git-broker,
context-ingest), over the internal token. A team without a connection of its own uses the shared credentials from the service's environment.
"""

import ipaddress
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

log = logging.getLogger("sif_contracts.connections")

PROVIDERS = ("jira", "github")
DEFAULT_GITHUB_API = "https://api.github.com"
DEFAULT_GITHUB_GIT = "https://github.com"


@dataclass(frozen=True)
class JiraCreds:
    base_url: str
    email: str
    token: str


@dataclass(frozen=True)
class GithubCreds:
    token: str
    api_url: str = DEFAULT_GITHUB_API
    git_url: str = DEFAULT_GITHUB_GIT


class ConnectionsUnavailable(Exception):
    """The portal could not say what a team's credentials are. Callers must not fall back to the shared account: that would act as the wrong identity."""


def _resolve(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def check_url(url: str, *, allow_private: bool = False, resolver: Callable[[str], list[str]] = _resolve) -> str | None:
    """Why this URL cannot be used for a connection, or None if it is fine. https only, and never an internal address unless allowed."""
    parts = urlparse(url.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        return "enter a full URL such as https://your-site.atlassian.net"
    if parts.username or parts.password:
        return "do not put credentials in the URL"
    if allow_private:
        return None
    if parts.scheme != "https":
        return "the URL must use https"
    try:
        addresses = resolver(parts.hostname)
    except OSError:
        return f"cannot resolve {parts.hostname}"
    for a in addresses:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return f"{parts.hostname} points at a private address ({a}); connections must use public hosts"
    return None


def normalise_url(url: str) -> str:
    return url.strip().rstrip("/")


def redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


class Credentials:
    """Fetches team credentials from portal-api and keeps them briefly, so a changed token is picked up without a restart.

    `None` means the team has no connection of its own (use the shared credentials). If the portal cannot be asked and nothing is cached,
    ConnectionsUnavailable is raised; if something is cached it is used even when stale."""

    def __init__(self, portal_url: str, internal_token: str, ttl: float = 60.0, client: httpx.AsyncClient | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.http = client or httpx.AsyncClient(base_url=portal_url, headers={"Authorization": f"Bearer {internal_token}"}, timeout=10)
        self.ttl, self.clock = ttl, clock
        self.cache: dict[tuple[str, str], tuple[float, JiraCreds | GithubCreds | None]] = {}

    async def get(self, team: str, provider: str) -> JiraCreds | GithubCreds | None:
        key = (team, provider)
        hit = self.cache.get(key)
        if hit and self.clock() - hit[0] < self.ttl:
            return hit[1]
        try:
            r = await self.http.get(f"/api/internal/connections/{quote(team, safe='')}/{provider}")  # a team name is never a path
            if r.status_code == 404:
                value = None
            elif r.status_code == 200:
                value = _parse(provider, r.json())
            else:
                raise ConnectionsUnavailable(f"portal-api answered {r.status_code}")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            if hit:
                log.warning("could not refresh %s credentials for %s (%s); using the last known ones", provider, team, type(e).__name__)
                return hit[1]
            raise ConnectionsUnavailable(f"could not fetch {provider} credentials for {team}: {type(e).__name__}") from e
        except ConnectionsUnavailable:
            if hit:
                return hit[1]
            raise
        self.cache[key] = (self.clock(), value)
        return value

    async def jira(self, team: str) -> JiraCreds | None:
        c = await self.get(team, "jira")
        return c if isinstance(c, JiraCreds) else None

    async def github(self, team: str) -> GithubCreds | None:
        c = await self.get(team, "github")
        return c if isinstance(c, GithubCreds) else None


def _parse(provider: str, data: dict) -> JiraCreds | GithubCreds:
    cfg = data.get("config") or {}
    if provider == "jira":
        return JiraCreds(base_url=cfg["base_url"], email=cfg["email"], token=data["secret"])
    return GithubCreds(token=data["secret"], api_url=cfg.get("api_url") or DEFAULT_GITHUB_API, git_url=cfg.get("git_url") or DEFAULT_GITHUB_GIT)
