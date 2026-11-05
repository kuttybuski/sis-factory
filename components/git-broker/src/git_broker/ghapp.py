"""GitHub App identity: short-lived installation tokens instead of a personal access token.

The app is installed only on the repositories the factory may touch. Each token is minted for one repository with only the permissions
git-broker uses (push sis/* branches, open and comment on PRs, read checks), and expires within the hour. A leaked token is good for
one repository for minutes, not for everything the person who created a PAT can reach. Branch protection or a ruleset on the default branch
is what stops the app from merging; see repo_posture."""

import base64
import json
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# What git-broker needs, and nothing else. Administration, workflows and secrets are deliberately absent.
PERMISSIONS = {"contents": "write", "pull_requests": "write", "issues": "write", "checks": "read", "statuses": "read", "metadata": "read"}
REFRESH_BEFORE = 300  # seconds before expiry a cached token is replaced


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id: str, private_key_pem: str, now: float | None = None) -> str:
    """The RS256 JWT GitHub expects from an app: valid for 9 minutes, backdated 60 s for clock drift."""
    now = int(now if now is not None else time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}).encode())
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())  # type: ignore[call-arg, union-attr]
    return f"{head}.{body}.{_b64(sig)}"


class AppError(Exception):
    pass


@dataclass
class AppTokens:
    app_id: str
    private_key_pem: str
    http: httpx.AsyncClient  # base_url = the GitHub API
    _cache: dict[str, tuple[str, float]] = field(default_factory=dict)
    _slug: str = ""

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {app_jwt(self.app_id, self.private_key_pem)}", "Accept": "application/vnd.github+json"}

    async def token(self, repo: str, now: float | None = None) -> str:
        """An installation token scoped to this one repository."""
        now = now if now is not None else time.time()
        if (hit := self._cache.get(repo)) and hit[1] - REFRESH_BEFORE > now:
            return hit[0]
        r = await self.http.get(f"/repos/{repo}/installation", headers=self._auth())
        if r.status_code == 404:
            raise AppError(f"the GitHub App is not installed on {repo}; install it there (and only where the factory may work)")
        if r.status_code >= 300:
            raise AppError(f"GitHub refused the app lookup for {repo}: {r.status_code}")
        name = repo.split("/", 1)[1]
        r = await self.http.post(f"/app/installations/{r.json()['id']}/access_tokens", headers=self._auth(),
                                 json={"repositories": [name], "permissions": PERMISSIONS})
        if r.status_code >= 300:
            raise AppError(f"GitHub refused an installation token for {repo}: {r.status_code} {r.text[:200]}")
        body = r.json()
        expires = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")).timestamp()
        self._cache[repo] = (body["token"], expires)
        return body["token"]

    async def bot_login(self) -> str:
        """The login the app's comments and commits carry ("name[bot]"); installation tokens cannot call /user."""
        if not self._slug:
            r = await self.http.get("/app", headers=self._auth())
            if r.status_code >= 300:
                raise AppError(f"GitHub refused GET /app: {r.status_code}")
            self._slug = r.json()["slug"]
        return f"{self._slug}[bot]"


def protection_problems(rules: list[dict] | None, classic: dict | None) -> list[str]:
    """Why the factory should not publish to a branch: nothing requires a reviewed pull request before changes land on it, so a token
    that can push could also merge. rules = GET /repos/{r}/rules/branches/{b} (rulesets); classic = GET .../branches/{b}/protection."""
    for rule in rules or []:
        if rule.get("type") == "pull_request" and int((rule.get("parameters") or {}).get("required_approving_review_count", 0)) >= 1:
            return []
    reviews = (classic or {}).get("required_pull_request_reviews") or {}
    if classic and int(reviews.get("required_approving_review_count", 0)) >= 1:
        return []
    if classic or rules:
        return ["the branch is protected but does not require an approving review before merging"]
    return ["the branch is not protected: anyone with push access, including the factory, could merge into it"]
