import dataclasses
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from injection_guard import Verdict, check_outbound, scan
from sif_contracts import posture
from sif_contracts import Settings as SifSettings
from sif_contracts.connections import ConnectionsUnavailable, Credentials, GithubCreds

from git_broker import ghapp, gitops


def github_client(base_url: str, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, timeout=30, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    posture.enforce("git-broker")  # SIF_ENV=production: refuse to start with unsafe settings (sif_contracts.posture)
    portal, internal = os.environ.get("PORTAL_API_URL", ""), os.environ.get("SIF_INTERNAL_TOKEN", "")
    # A team can have its own GitHub connection (saved on the dashboard). Without a portal to ask, every team uses the shared token.
    app.state.creds = Credentials(portal, internal, ttl=SifSettings().connection_cache_seconds) if portal and internal else None
    app.state.team_client = lambda c: github_client(c.api_url, c.token)  # tests replace this
    app.state.settings = gitops.Settings()
    app.state.allowed_hosts = {h for h in os.environ.get("SIF_ALLOWED_LINK_HOSTS", "github.com").split(",") if h}
    app.state.settings.data_dir.mkdir(parents=True, exist_ok=True)
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    app.state.http = github_client(api, app.state.settings.token)
    # A GitHub App, when configured, replaces the shared token: one short-lived token per repository (ghapp.py).
    app_id, key = os.environ.get("GITHUB_APP_ID", ""), os.environ.get("GITHUB_APP_PRIVATE_KEY", "").replace("\\n", "\n")
    if not key and (path := os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")):  # the name .env.example uses; _FILE works too
        key = Path(path).read_text()
    app.state.app_http = httpx.AsyncClient(base_url=api, timeout=30) if app_id and key else None
    app.state.app_tokens = ghapp.AppTokens(app_id, key, app.state.app_http) if app.state.app_http else None
    # Refuse to publish onto a branch that does not require an approving review (on by default in production, docs/security.md).
    app.state.require_review = os.environ.get("SIF_REQUIRE_PROTECTED_BRANCH", "true" if os.environ.get("SIF_ENV") == "production" else "false").lower() in ("1", "true", "yes")
    app.state.protection_cache = {}
    yield
    await app.state.http.aclose()
    if app.state.app_http:
        await app.state.app_http.aclose()


app = FastAPI(title="sif git-broker", lifespan=lifespan)


def internal(authorization: str | None = Header(default=None)) -> None:
    token = os.environ.get("SIF_INTERNAL_TOKEN", "")
    if not token:
        raise HTTPException(503, "SIF_INTERNAL_TOKEN is not configured")
    if not secrets.compare_digest((authorization or "").removeprefix("Bearer "), token):
        raise HTTPException(401, "invalid token")


@app.get("/healthz")
def healthz():
    return {"ok": True}


class AppCreds(GithubCreds):
    """A GitHub App installation token for one repository."""


async def github_for(team: str | None, repo: str) -> tuple[gitops.Settings, httpx.AsyncClient, object | None]:
    """The git settings and GitHub API client to use for a team: its own connection if it has one, otherwise the GitHub App's token for
    the repository, otherwise the shared token. If the portal cannot say whether the team has one, the request is refused rather than made
    as the shared account. The third value is set when the client must be closed after the request."""
    shared = app.state.settings
    creds = None
    if team and app.state.creds is not None:
        try:
            creds = await app.state.creds.github(team)
        except ConnectionsUnavailable as e:
            raise HTTPException(503, f"could not look up the GitHub connection for {team}: {e}") from None
    if creds is not None:
        return dataclasses.replace(shared, token=creds.token, remote_base=creds.git_url), app.state.team_client(creds), creds
    if app.state.app_tokens is not None:
        try:
            token = await app.state.app_tokens.token(gitops.check_repo(repo))
        except ghapp.AppError as e:
            raise HTTPException(502, str(e)) from None
        except gitops.Rejected as e:
            raise HTTPException(422, str(e)) from None
        c = AppCreds(token=token, api_url=str(app.state.app_http.base_url).rstrip("/"))
        return dataclasses.replace(shared, token=token), app.state.team_client(c), c
    return shared, app.state.http, None


@asynccontextmanager
async def github(team: str | None, repo: str) -> AsyncIterator[tuple[gitops.Settings, httpx.AsyncClient, object | None]]:
    """github_for for the length of one request: refused patches become 422, git failures 502, and a per-request client is closed."""
    s, client, creds = await github_for(team, repo)
    try:
        yield s, client, creds
    except gitops.Rejected as e:
        raise HTTPException(422, str(e)) from None
    except gitops.GitError as e:
        raise HTTPException(502, str(e)) from None
    finally:
        if creds:
            await client.aclose()


async def bot_login(client: httpx.AsyncClient) -> str:
    if app.state.app_tokens is not None:
        try:
            return await app.state.app_tokens.bot_login()
        except ghapp.AppError:
            pass  # a team with its own token: /user works
    return (await _get(client, "/user")).get("login", "")


PROTECTION_TTL = 600


async def check_protection(client: httpx.AsyncClient, repo: str, branch: str) -> list[str]:
    """protection_problems for a branch, cached for ten minutes. A branch whose rules cannot be read counts as unprotected."""
    key = f"{repo}@{branch}"
    if (hit := app.state.protection_cache.get(key)) and hit[1] > time.time():
        return hit[0]
    rules = await client.get(f"/repos/{repo}/rules/branches/{branch}")
    classic = await client.get(f"/repos/{repo}/branches/{branch}/protection")
    problems = ghapp.protection_problems(rules.json() if rules.status_code == 200 else None, classic.json() if classic.status_code == 200 else None)
    if problems and classic.status_code == 403 and rules.status_code != 200:
        problems = ["the factory cannot read the branch's protection (GitHub returned 403)"]
    app.state.protection_cache[key] = (problems, time.time() + PROTECTION_TTL)
    return problems


ADMIN = "the factory's credentials have admin rights on this repository (an admin can merge without the required review); use a GitHub App or a token without admin"


async def has_admin(client: httpx.AsyncClient, repo: str) -> bool:
    """GitHub reports permissions for user tokens (a team's connection, GITHUB_TOKEN); an App's installation token never has admin."""
    r = await client.get(f"/repos/{repo}")
    return r.status_code == 200 and bool((r.json().get("permissions") or {}).get("admin"))


async def refuse_admin(client: httpx.AsyncClient, repo: str) -> None:
    if posture.production() and await has_admin(client, repo):
        raise gitops.Rejected(f"{repo}: {ADMIN}. The factory does not push with admin rights in production.")


class CheckoutIn(BaseModel):
    repo: str
    ref: str | None = None
    team: str | None = None


@app.post("/v1/checkout", dependencies=[Depends(internal)])
async def checkout(body: CheckoutIn):
    async with github(body.team, body.repo) as (s, _, _):
        return await gitops.checkout(s, body.repo, body.ref)


class DiffIn(BaseModel):
    repo: str
    base: str
    head: str
    team: str | None = None


@app.post("/v1/diff", dependencies=[Depends(internal)])
async def diff(body: DiffIn):
    """What was merged, as a diff from where the factory started: the reference a replay is scored against."""
    async with github(body.team, body.repo) as (s, _, _):
        return {"diff": await gitops.diff(s, body.repo, body.base, body.head)}


class FileIn(BaseModel):
    repo: str
    commit: str
    path: str
    team: str | None = None


@app.post("/v1/file", dependencies=[Depends(internal)])
async def read_file(body: FileIn):
    async with github(body.team, body.repo) as (s, _, _):
        content = await gitops.read_file(s, body.repo, body.commit, body.path)
    if content is None:
        raise HTTPException(404, f"{body.path} does not exist at {body.commit[:8]}")
    return {"content": content}


@app.get("/v1/archive/{owner}/{name}/{commit}.tar.gz", dependencies=[Depends(internal)])
async def archive(owner: str, name: str, commit: str):
    try:
        data = await gitops.archive(app.state.settings, f"{owner}/{name}", commit)
    except gitops.Rejected as e:
        raise HTTPException(422, str(e))
    except gitops.GitError as e:
        raise HTTPException(404, str(e))
    return Response(data, media_type="application/gzip")


class PublishIn(BaseModel):
    repo: str
    base_commit: str
    branch: str
    patch: str
    title: str = Field(max_length=200)
    body: str = Field("", max_length=20_000)
    base_branch: str = "main"
    draft: bool = True
    jira_key: str = ""
    team: str | None = None


def screen(allowed_hosts: set[str], *texts: str) -> None:
    """Everything that becomes visible on GitHub is checked for injected instructions and leaked secrets first."""
    for t in texts:
        if not check_outbound(t, allowed_hosts=allowed_hosts).ok:
            raise HTTPException(422, "blocked: the content looks like it contains a secret or an exfiltration link")
        if scan(t, "pr_description").verdict is Verdict.BLOCK:
            raise HTTPException(422, "blocked: the content looks like a prompt-injection attempt")


@app.post("/v1/publish", dependencies=[Depends(internal)])
async def publish(body: PublishIn):
    """Pushes the agent's patch to a sif/* branch and opens a DRAFT pull request. Never merges."""
    async with github(body.team, body.repo) as (s, client, _):
        gitops.check_repo(body.repo)
        screen(app.state.allowed_hosts, body.title, body.body, body.patch)
        await refuse_admin(client, body.repo)
        if app.state.require_review and (problems := await check_protection(client, body.repo, body.base_branch)):
            raise gitops.Rejected(f"{body.repo}@{body.base_branch}: {problems[0]}. Protect it with a required review before the factory works there "
                                  "(SIF_REQUIRE_PROTECTED_BRANCH).")
        message = f"{body.title}\n\n{'Jira: ' + body.jira_key if body.jira_key else ''}".strip()
        sha = await gitops.publish_branch(s, body.repo, body.base_commit, body.branch, body.patch, message)
        r = await client.post(f"/repos/{body.repo}/pulls", json={
            "title": body.title, "head": body.branch, "base": body.base_branch, "body": body.body, "draft": body.draft})
    if r.status_code >= 300:
        raise HTTPException(502, f"GitHub refused the pull request: {r.status_code} {r.text[:300]}")
    pr = r.json()
    return {"pr_url": pr["html_url"], "number": pr["number"], "commit": sha, "branch": body.branch}


class UpdateIn(BaseModel):
    repo: str
    base_commit: str  # the head of the PR branch the agent worked from: the push is a fast-forward, never a force-push
    branch: str
    patch: str
    message: str = Field(max_length=2000)
    pr_number: int
    comment: str = Field("", max_length=20_000)
    team: str | None = None


@app.post("/v1/update", dependencies=[Depends(internal)])
async def update(body: UpdateIn):
    """Pushes a revision onto an existing sif/* PR branch and says on the PR what changed."""
    async with github(body.team, body.repo) as (s, client, _):
        gitops.check_repo(body.repo)
        screen(app.state.allowed_hosts, body.message, body.comment, body.patch)
        await refuse_admin(client, body.repo)
        sha = await gitops.publish_branch(s, body.repo, body.base_commit, body.branch, body.patch, body.message)
        if body.comment:
            await client.post(f"/repos/{body.repo}/issues/{body.pr_number}/comments", json={"body": body.comment})
    return {"commit": sha}


class CommentIn(BaseModel):
    repo: str
    pr_number: int
    body: str = Field(max_length=20_000)
    team: str | None = None


@app.post("/v1/comment", dependencies=[Depends(internal)])
async def comment(body: CommentIn):
    async with github(body.team, body.repo) as (_, client, _):
        gitops.check_repo(body.repo)
        screen(app.state.allowed_hosts, body.body)
        r = await client.post(f"/repos/{body.repo}/issues/{body.pr_number}/comments", json={"body": body.body})
    if r.status_code >= 300:
        raise HTTPException(502, f"GitHub refused the comment: {r.status_code}")
    return {"ok": True}


async def _get(client: httpx.AsyncClient, path: str) -> list | dict:
    r = await client.get(path, params={"per_page": 100})
    if r.status_code >= 300:
        raise HTTPException(502, f"GitHub {path}: {r.status_code} {r.text[:200]}")
    return r.json()


@app.get("/v1/pr/{owner}/{name}/{number}", dependencies=[Depends(internal)])
async def pr_status(owner: str, name: str, number: int, team: str | None = None, mention: str = "@sif"):
    """Where a PR stands: open, merged or closed; what people asked for on it; and how CI did on its head commit."""
    repo = gitops.check_repo(f"{owner}/{name}")
    async with github(team, repo) as (s, client, _):
        pr = await _get(client, f"/repos/{repo}/pulls/{number}")
        bot = await bot_login(client)
        reviews, inline, comments = (await _get(client, f"/repos/{repo}/pulls/{number}/reviews"), await _get(client, f"/repos/{repo}/pulls/{number}/comments"),
                                     await _get(client, f"/repos/{repo}/issues/{number}/comments"))
        head = pr["head"]["sha"]
        runs = (await _get(client, f"/repos/{repo}/commits/{head}/check-runs")).get("check_runs", [])
        statuses = (await _get(client, f"/repos/{repo}/commits/{head}/status")).get("statuses", [])
        merged = bool(pr.get("merged") or pr.get("merged_at"))
        fixups = gitops.human_commits(bot, s.author_name, await _get(client, f"/repos/{repo}/pulls/{number}/commits")) if merged else []
    state = "merged" if merged else pr.get("state", "open")
    return {"state": state, "merged_by": (pr.get("merged_by") or {}).get("login", ""), "head_sha": head, "head_ref": pr["head"]["ref"], "feedback": gitops.collect_feedback(bot, reviews, inline, comments, mention, gitops.trusted_associations()),
            "ci": gitops.ci_result(head, runs, statuses), "fixups": fixups}


@app.get("/v1/posture/{owner}/{name}", dependencies=[Depends(internal)])
async def repo_posture(owner: str, name: str, team: str | None = None):
    """Is this repository safe for the factory to work in: which identity it uses, and whether the default branch requires an approving
    review before anything merges (the guarantee that a factory PR is never merged without a person)."""
    repo = gitops.check_repo(f"{owner}/{name}")
    async with github(team, repo) as (_, client, creds):
        r = await client.get(f"/repos/{repo}")
        if r.status_code >= 300:
            return {"repo": repo, "ok": False, "problems": [f"the factory cannot read {repo} (GitHub {r.status_code})"]}
        info = r.json()
        branch = info.get("default_branch", "main")
        problems = await check_protection(client, repo, branch)
        if (info.get("permissions") or {}).get("admin"):
            problems = [*problems, ADMIN]
    identity = "GitHub App" if isinstance(creds, AppCreds) else "team connection" if creds else "shared token"
    return {"repo": repo, "branch": branch, "identity": identity, "ok": not problems, "problems": problems}
