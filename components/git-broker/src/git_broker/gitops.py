"""Git operations by shelling out to git. Credentials travel in the environment, never in arguments or URLs."""

import asyncio
import base64
import fnmatch
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

BRANCH_PREFIX = "sis/"
MAX_PATCH_BYTES = 1_000_000
# CI definitions run with the repository's secrets, and CODEOWNERS decides who must review: a change to either is a change to who can do
# what, so a person makes it.
DEFAULT_PROTECTED = (".github/workflows/**,.github/actions/**,CODEOWNERS,.github/CODEOWNERS,docs/CODEOWNERS,.sis/**,.gitlab-ci.yml,.gitlab/**,"
                     "Jenkinsfile,*/Jenkinsfile,.circleci/**,azure-pipelines.yml,.azure-pipelines/**,.buildkite/**,bitbucket-pipelines.yml,.travis.yml")
# Manifests and lockfiles, matched by file name: a new or changed dependency is code from outside the company (typosquats, licences).
DEFAULT_DEPENDENCY_FILES = ("package.json,package-lock.json,npm-shrinkwrap.json,yarn.lock,pnpm-lock.yaml,bun.lockb,requirements*.txt,pyproject.toml,"
                            "poetry.lock,uv.lock,Pipfile,Pipfile.lock,setup.py,setup.cfg,go.mod,go.sum,Cargo.toml,Cargo.lock,pom.xml,build.gradle,"
                            "build.gradle.kts,settings.gradle,settings.gradle.kts,gradle.lockfile,Gemfile,Gemfile.lock,composer.json,composer.lock,"
                            "*.csproj,packages.config,packages.lock.json,Directory.Packages.props,Package.swift,Package.resolved,Podfile,Podfile.lock")
_DIFF_FILE = re.compile(r"^diff --git a/(.*?) b/(.*)$", re.MULTILINE)
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^sis/[A-Za-z0-9._/-]{1,100}$")


class GitError(Exception):
    pass


class Rejected(Exception):
    """The request is not allowed (protected path, bad branch, oversized or unsafe patch)."""


def _env_default(name: str, default: str):
    return field(default_factory=lambda: os.environ.get(name, default))  # read when Settings() is built, not at import time


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("GIT_BROKER_DATA", "/data")))
    remote_base: str = _env_default("GIT_REMOTE_BASE", "https://github.com")  # tests point this at a local directory
    token: str = _env_default("GITHUB_TOKEN", "")
    protected: tuple[str, ...] = field(default_factory=lambda: tuple(p for p in os.environ.get("SIS_PROTECTED_PATHS", DEFAULT_PROTECTED).split(",") if p))
    dependency_files: tuple[str, ...] = field(default_factory=lambda: () if os.environ.get("SIS_ALLOW_DEPENDENCY_CHANGES", "").lower() in ("1", "true", "yes")
                                              else tuple(p for p in os.environ.get("SIS_DEPENDENCY_FILES", DEFAULT_DEPENDENCY_FILES).split(",") if p))
    max_files: int = field(default_factory=lambda: int(os.environ.get("SIS_MAX_CHANGED_FILES", "50")))
    max_lines: int = field(default_factory=lambda: int(os.environ.get("SIS_MAX_CHANGED_LINES", "3000")))
    author_name: str = _env_default("SIS_GIT_AUTHOR_NAME", "sis-bot")
    author_email: str = _env_default("SIS_GIT_AUTHOR_EMAIL", "sis-bot@users.noreply.github.com")


def check_repo(repo: str) -> str:
    if not _REPO.match(repo) or ".." in repo:
        raise Rejected(f"'{repo}' is not an owner/name repository")
    return repo


def check_branch(branch: str) -> str:
    if not _BRANCH.match(branch) or ".." in branch or branch.endswith(("/", ".lock")):
        raise Rejected(f"branch must look like '{BRANCH_PREFIX}<name>': agents can never push to other branches")
    return branch


def patch_paths(patch: str) -> set[str]:
    paths = set()
    for a, b in _DIFF_FILE.findall(patch):
        paths |= {a, b}
    return paths


def changed_lines(patch: str) -> int:
    return sum(1 for l in patch.splitlines() if l[:1] in "+-" and not l.startswith(("+++ ", "--- ")))


def check_patch(patch: str, protected: tuple[str, ...], dependency_files: tuple[str, ...] = (), max_files: int = 0, max_lines: int = 0) -> set[str]:
    """Refuses what a person should do or look at first: protected paths, dependency changes, and changes too large to review well."""
    if not patch.strip():
        raise Rejected("the patch is empty")
    if len(patch.encode()) > MAX_PATCH_BYTES:
        raise Rejected(f"the patch is larger than {MAX_PATCH_BYTES} bytes")
    paths = patch_paths(patch)
    if not paths:
        raise Rejected("the patch is not a git diff")
    for p in paths:
        if p.startswith("/") or ".." in Path(p).parts:
            raise Rejected(f"unsafe path in patch: {p}")
        if any(fnmatch.fnmatch(p, pat) for pat in protected):
            raise Rejected(f"the patch changes a protected path ({p}); ask a human to make that change")
        if any(fnmatch.fnmatch(Path(p).name, pat) for pat in dependency_files):
            raise Rejected(f"the patch changes dependencies ({p}); a person adds or upgrades dependencies (SIS_ALLOW_DEPENDENCY_CHANGES)")
    files = len(paths - {"/dev/null"})
    if max_files and files > max_files:
        raise Rejected(f"the patch changes {files} files, more than {max_files} (SIS_MAX_CHANGED_FILES); split the ticket into smaller ones")
    if max_lines and (n := changed_lines(patch)) > max_lines:
        raise Rejected(f"the patch changes {n} lines, more than {max_lines} (SIS_MAX_CHANGED_LINES); split the ticket into smaller ones")
    return paths


def _env(s: Settings, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {"PATH": os.environ["PATH"], "HOME": str(s.data_dir), "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": s.author_name, "GIT_AUTHOR_EMAIL": s.author_email,
           "GIT_COMMITTER_NAME": s.author_name, "GIT_COMMITTER_EMAIL": s.author_email}
    if s.token and s.remote_base.startswith("https://"):
        header = "Authorization: Basic " + base64.b64encode(f"x-access-token:{s.token}".encode()).decode()
        env |= {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": header}
    return env | (extra or {})


async def git(s: Settings, *args: str, cwd: Path | None = None, input: bytes | None = None, timeout: int = 300) -> bytes:
    proc = await asyncio.create_subprocess_exec("git", *args, cwd=cwd, env=_env(s), stdin=asyncio.subprocess.PIPE if input is not None else None,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(input), timeout)
    except TimeoutError:
        proc.kill()
        raise GitError(f"git {args[0]} timed out") from None
    if proc.returncode:
        msg = err.decode(errors="replace").strip()
        if s.token:
            msg = msg.replace(s.token, "***")
        raise GitError(f"git {args[0]} failed: {msg[:500]}")
    return out


def mirror_path(s: Settings, repo: str) -> Path:
    return s.data_dir / "mirrors" / (check_repo(repo).replace("/", "__") + ".git")


async def checkout(s: Settings, repo: str, ref: str | None = None) -> dict[str, str]:
    """Keeps a bare mirror up to date and returns the commit to work from."""
    mirror = mirror_path(s, repo)
    url = f"{s.remote_base.rstrip('/')}/{repo}" + ("" if s.remote_base.startswith("file://") else ".git")
    if not mirror.exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        await git(s, "clone", "--mirror", url, str(mirror))
    else:
        await git(s, "remote", "update", "--prune", cwd=mirror)
    head = ref or (await git(s, "symbolic-ref", "--short", "HEAD", cwd=mirror)).decode().strip()
    commit = (await git(s, "rev-parse", "--verify", f"{head}^{{commit}}", cwd=mirror)).decode().strip()
    return {"commit": commit, "ref": head}


async def archive(s: Settings, repo: str, commit: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Rejected("commit must be a full 40-character sha")
    return await git(s, "archive", "--format=tar.gz", commit, cwd=mirror_path(s, repo))


MAX_DIFF = 400_000


async def diff(s: Settings, repo: str, base: str, head: str) -> str:
    """The change between two commits, after bringing the mirror up to date. For replay cases: base commit to merged head."""
    for sha in (base, head):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise Rejected("commits must be full 40-character shas")
    await checkout(s, repo)
    out = (await git(s, "diff", "--no-color", "--no-ext-diff", base, head, cwd=mirror_path(s, repo))).decode(errors="replace")
    if len(out) > MAX_DIFF:
        raise Rejected(f"the change is larger than {MAX_DIFF // 1000} kB, too large to use as a replay reference")
    return out


async def read_file(s: Settings, repo: str, commit: str, path: str) -> str | None:
    """A file's content at a commit, or None if it does not exist there. Used for the knowledge PR's CLAUDE.md."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Rejected("commit must be a full 40-character sha")
    if path.startswith("/") or ".." in path.split("/") or not re.fullmatch(r"[\w./-]+", path):
        raise Rejected("invalid path")
    await checkout(s, repo)
    try:
        return (await git(s, "show", f"{commit}:{path}", cwd=mirror_path(s, repo))).decode(errors="replace")
    except GitError:
        return None


async def publish_branch(s: Settings, repo: str, base_commit: str, branch: str, patch: str, message: str) -> str:
    """Applies the patch on top of base_commit in a throwaway worktree and pushes it. Returns the new commit sha."""
    check_branch(branch)
    check_patch(patch, s.protected, s.dependency_files, s.max_files, s.max_lines)
    mirror = mirror_path(s, repo)
    work = s.data_dir / "work" / f"{repo.replace('/', '__')}-{os.urandom(4).hex()}"
    try:
        await git(s, "worktree", "add", "--detach", str(work), base_commit, cwd=mirror)
        try:
            await git(s, "apply", "--index", "--whitespace=nowarn", "-", cwd=work, input=patch.encode())
        except GitError as e:
            raise Rejected(f"the patch does not apply to {base_commit[:8]}: {e}") from e
        await git(s, "commit", "-m", message, cwd=work)
        sha = (await git(s, "rev-parse", "HEAD", cwd=work)).decode().strip()
        await git(s, "-c", "remote.origin.mirror=false", "push", "origin", f"{sha}:refs/heads/{branch}", cwd=mirror)  # a --mirror clone refuses refspecs otherwise
        return sha
    finally:
        await git(s, "worktree", "remove", "--force", str(work), cwd=mirror) if work.exists() else None
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- reading a PR back
FAILED = {"failure", "timed_out", "action_required", "startup_failure"}


TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")


def trusted_associations() -> tuple[str, ...]:
    return tuple(a.strip().upper() for a in os.environ.get("SIS_TRUSTED_ASSOCIATIONS", ",".join(TRUSTED)).split(",") if a.strip())


def collect_feedback(bot: str, reviews: list[dict], inline: list[dict], comments: list[dict], mention: str, trusted: tuple[str, ...] = TRUSTED) -> list[dict]:
    """What people asked for on a PR, oldest first. A review that requests changes, and its inline comments, are always for the agent;
    anything else only when it mentions the bot, so people can still talk to each other on the PR. Approvals and the bot's own
    comments are never feedback, and neither is anything from outside the organisation (GitHub's author_association): anyone can
    comment on a public repository, and the feedback becomes the agent's next instructions and, if it recurs, a lesson."""
    login = lambda c: (c.get("user") or {}).get("login", "")  # noqa: E731
    who = lambda c: login(c) if (c.get("author_association") or "").upper() in trusted else bot  # noqa: E731 - untrusted counts as the bot: skipped
    asks = lambda text: mention.lower() in (text or "").lower()  # noqa: E731
    changes = {r["id"] for r in reviews if r.get("state") == "CHANGES_REQUESTED" and who(r) != bot}
    out = []
    for r in reviews:
        if who(r) != bot and (r.get("body") or "").strip() and (r["id"] in changes or (r.get("state") == "COMMENTED" and asks(r["body"]))):
            out.append({"id": f"review-{r['id']}", "author": who(r), "body": r["body"].strip(), "kind": "review", "path": "", "at": r.get("submitted_at") or ""})
    for c in inline:
        if who(c) != bot and (c.get("pull_request_review_id") in changes or asks(c.get("body"))):
            where = f"{c.get('path', '')}:{c.get('line') or c.get('original_line') or ''}".rstrip(":")
            out.append({"id": f"inline-{c['id']}", "author": who(c), "body": (c.get("body") or "").strip(), "kind": "inline", "path": where, "at": c.get("created_at") or ""})
    for c in comments:
        if who(c) != bot and asks(c.get("body")):
            out.append({"id": f"comment-{c['id']}", "author": who(c), "body": (c.get("body") or "").strip(), "kind": "comment", "path": "", "at": c.get("created_at") or ""})
    return sorted(out, key=lambda f: f["at"])


def human_commits(bot: str, bot_name: str, commits: list[dict]) -> list[dict]:
    """Commits on a PR branch that people made: what the agent did not get right before the merge. Merge commits (bringing the base branch
    in) are not corrections, and the factory's own commits carry its git identity or the bot's login."""
    out = []
    for c in commits:
        login = (c.get("author") or {}).get("login", "")
        name = ((c.get("commit") or {}).get("author") or {}).get("name", "")
        if len(c.get("parents") or []) > 1 or login == bot or name == bot_name:
            continue
        out.append({"sha": c.get("sha", ""), "author": login or name, "message": ((c.get("commit") or {}).get("message") or "").split("\n")[0][:200]})
    return out


def ci_result(head: str, runs: list[dict], statuses: list[dict]) -> dict:
    """failure if any check run or commit status failed, pending while any is still running, success when all passed, none without CI."""
    failures = [{"name": r.get("name", "check"), "summary": "\n".join(x for x in ((r.get("output") or {}).get("title"), (r.get("output") or {}).get("summary")) if x)[:1500]}
                for r in runs if r.get("status") == "completed" and r.get("conclusion") in FAILED]
    failures += [{"name": s.get("context", "status"), "summary": (s.get("description") or "")[:500]} for s in statuses if s.get("state") in ("failure", "error")]
    pending = any(r.get("status") != "completed" for r in runs) or any(s.get("state") == "pending" for s in statuses)
    state = "failure" if failures else "pending" if pending else "success" if runs or statuses else "none"
    return {"state": state, "sha": head, "failures": failures}
