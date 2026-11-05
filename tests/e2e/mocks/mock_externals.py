"""A stand-in for Jira Cloud, the GitHub API and an LLM provider, so the whole system can run without touching real accounts.

Jira: just the endpoints jira-connector uses. GitHub: pull-request creation (the git remote is a local bare repository).
LLM: /v1/messages with a configurable cost. /mock/* routes let a test drive the scenario."""

import os
import re
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, Request

REMOTES = Path(os.environ.get("MOCK_REMOTES", "/remotes"))
BOT = {"accountId": "bot-account", "displayName": "sif", "emailAddress": "bot@example.com"}
app = FastAPI()
issues: dict[str, dict] = {}
pulls: list[dict] = []
llm_cost = {"usd": 0.6}


def git(*args, cwd=None):
    env = {"PATH": os.environ["PATH"], "GIT_AUTHOR_NAME": "seed", "GIT_AUTHOR_EMAIL": "s@s", "GIT_COMMITTER_NAME": "seed", "GIT_COMMITTER_EMAIL": "s@s", "HOME": "/tmp"}
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


def seed_repo() -> None:
    bare = REMOTES / "acme" / "api"
    if bare.exists():
        return
    work = Path("/tmp/seed")
    work.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", cwd=work)
    (work / "src").mkdir(exist_ok=True)
    (work / "src" / "app.py").write_text("def add(a, b):\n    return a + b\n")
    (work / "README.md").write_text("# acme api\n\nBilling helpers. Rounding uses banker's rounding.\n")
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "initial", cwd=work)
    bare.parent.mkdir(parents=True, exist_ok=True)
    git("clone", "-q", "--bare", str(work), str(bare))
    subprocess.run(["chown", "-R", "65534:65534", str(REMOTES)], check=True)  # git-broker runs as 'nobody'


seed_repo()


def adf(text: str) -> dict:
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}, {"type": "hardBreak"}]} for line in text.split("\n")]}


def text_of(node) -> str:
    if isinstance(node, str):
        return node
    if node.get("type") == "text":
        return node["text"]
    if node.get("type") == "hardBreak":
        return "\n"
    if node.get("type") == "mention":
        return f"[~accountid:{node['attrs']['id']}]"
    return "".join(text_of(c) for c in node.get("content", [])) + ("\n" if node.get("type") == "paragraph" else "")


# ---------------------------------------------------------------- Jira
auth_log: list[dict] = []  # who called what: lets a test check which account the connector used
BAD = "bad-token"


def credentials_of(request: Request) -> tuple[str, str]:
    """(email, token) from Basic auth, or ("", bearer token) from a Bearer header."""
    import base64

    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        email, _, token = base64.b64decode(header[6:]).decode().partition(":")
        return email, token
    return "", header.removeprefix("Bearer ")


@app.get("/rest/api/3/myself")
def myself(request: Request):
    from fastapi.responses import JSONResponse

    email, token = credentials_of(request)
    auth_log.append({"path": "myself", "email": email})
    return JSONResponse({}, status_code=401) if token == BAD else BOT


@app.get("/rest/api/3/project/{key}")
def project(key: str):
    return {"key": key}


def matches(issue: dict, jql: str) -> bool:
    labels = set(issue["labels"])
    for m in re.finditer(r'labels = "([^"]+)"', jql):
        if m.group(1) not in labels:
            return False
    for m in re.finditer(r'labels != "([^"]+)"', jql):
        if m.group(1) in labels:
            return False
    if m := re.search(r"labels in \(([^)]*)\)", jql):
        if not labels & {x.strip().strip('"') for x in m.group(1).split(",")}:
            return False
    return True


@app.post("/rest/api/3/search/jql")
async def search(request: Request):
    body = await request.json()
    auth_log.append({"path": "search", "email": credentials_of(request)[0], "jql": body["jql"]})
    out = [{"key": k, "fields": {"summary": i["summary"], "description": adf(i["description"]), "labels": i["labels"], "reporter": i["reporter"], "assignee": None,
                                 "issuetype": {"name": i.get("type", "Task")}, **({"parent": {"key": i["parent"]}} if i.get("parent") else {})}}
           for k, i in issues.items() if matches(i, body["jql"])]
    return {"issues": out, "isLast": True}


@app.get("/rest/api/3/issue/{key}/comment")
def comments(key: str):
    cs = issues[key]["comments"]
    return {"total": len(cs), "comments": [{"id": str(c["id"]), "author": c["author"], "body": adf(c["text"]), "created": c["created"]} for c in cs]}


@app.post("/rest/api/3/issue/{key}/comment", status_code=201)
async def add_comment(key: str, request: Request):
    body = await request.json()
    auth_log.append({"path": "comment", "key": key, "email": credentials_of(request)[0]})
    cs = issues[key]["comments"]
    cs.append({"id": len(cs) + 1, "author": BOT, "text": text_of(body["body"]).strip(), "created": str(time.time())})
    return {"id": str(len(cs))}


@app.post("/rest/api/3/issue", status_code=201)
async def create_issue_api(request: Request):
    """What the connector calls to create the stories of a split epic."""
    f = (await request.json())["fields"]
    key = f"{f['project']['key']}-{100 + len(issues)}"
    issues[key] = {"summary": f["summary"], "description": text_of(f["description"]).strip(), "labels": f.get("labels", []), "type": f["issuetype"]["name"],
                   "parent": (f.get("parent") or {}).get("key"), "reporter": BOT, "comments": []}
    return {"id": str(len(issues)), "key": key}


@app.put("/rest/api/3/issue/{key}", status_code=204)
async def update(key: str, request: Request):
    for op in (await request.json())["update"]["labels"]:
        labels = issues[key]["labels"]
        if "add" in op and op["add"] not in labels:
            labels.append(op["add"])
        if "remove" in op and op["remove"] in labels:
            labels.remove(op["remove"])


# ---------------------------------------------------------------- GitHub
@app.get("/user")
def github_user(request: Request):
    from fastapi.responses import JSONResponse

    return JSONResponse({}, status_code=401) if credentials_of(request)[1] == BAD else {"login": "mock-gh"}


@app.get("/repos/{owner}/{name}")
def github_repo(owner: str, name: str):
    from fastapi.responses import JSONResponse

    return {"full_name": f"{owner}/{name}"} if (owner, name) == ("acme", "api") else JSONResponse({}, status_code=404)


@app.post("/repos/{owner}/{name}/pulls", status_code=201)
async def create_pull(owner: str, name: str, request: Request):
    body = await request.json()
    pr = {**body, "repo": f"{owner}/{name}", "number": len(pulls) + 1, "state": "open", "merged": False, "reviews": [], "comments": []}
    pr["html_url"] = f"http://mock-externals:8080/{owner}/{name}/pull/{pr['number']}"
    pulls.append(pr)
    return pr


def bare_rev(owner: str, name: str, ref: str) -> str:
    bare = REMOTES / owner / name
    return subprocess.run(["git", "-c", "safe.directory=*", "--git-dir", str(bare), "rev-parse", f"refs/heads/{ref}"], capture_output=True, text=True).stdout.strip()


checks: dict[str, list[dict]] = {}  # head sha -> check runs, set by a test


@app.get("/repos/{owner}/{name}/pulls/{number}")
def get_pull(owner: str, name: str, number: int):
    pr = pulls[number - 1]
    return {**pr, "head": {"sha": bare_rev(owner, name, pr["head"]), "ref": pr["head"]}}


@app.get("/repos/{owner}/{name}/pulls/{number}/commits")
def get_pull_commits(owner: str, name: str, number: int):
    return pulls[number - 1].get("commits", [])  # people's commits on the branch, set by a test with /mock/pulls/{n}/fixup


@app.get("/repos/{owner}/{name}/pulls/{number}/reviews")
def get_reviews(owner: str, name: str, number: int):
    return pulls[number - 1]["reviews"]


@app.get("/repos/{owner}/{name}/pulls/{number}/comments")
def get_inline(owner: str, name: str, number: int):
    return []


@app.get("/repos/{owner}/{name}/issues/{number}/comments")
def get_pr_comments(owner: str, name: str, number: int):
    return pulls[number - 1]["comments"]


@app.post("/repos/{owner}/{name}/issues/{number}/comments", status_code=201)
async def add_pr_comment(owner: str, name: str, number: int, request: Request):
    cs = pulls[number - 1]["comments"]
    cs.append({"id": 1000 + len(cs), "user": {"login": "mock-gh"}, "body": (await request.json())["body"], "created_at": f"{time.time():.6f}"})
    return cs[-1]


@app.get("/repos/{owner}/{name}/commits/{sha}/check-runs")
def get_checks(owner: str, name: str, sha: str):
    return {"check_runs": checks.get(sha, [])}


@app.get("/repos/{owner}/{name}/commits/{sha}/status")
def get_status(owner: str, name: str, sha: str):
    return {"statuses": []}


# ---------------------------------------------------------------- LLM provider
@app.post("/v1/messages")
async def messages(request: Request):
    from fastapi.responses import JSONResponse

    await request.json()
    return JSONResponse({"content": [{"type": "text", "text": "ok"}], "usage": {"input_tokens": 10, "output_tokens": 5}}, headers={"x-litellm-response-cost": str(llm_cost["usd"])})


# ---------------------------------------------------------------- scenario control
@app.post("/mock/issues")
async def create_issue(request: Request):
    b = await request.json()
    issues[b["key"]] = {"summary": b["summary"], "description": b["description"], "labels": b.get("labels", ["sif"]), "type": b.get("type", "Task"), "reporter": {"accountId": "human-1", "displayName": "Rae"}, "comments": []}
    return {"ok": True}


@app.post("/mock/issues/{key}/comments")
async def human_comment(key: str, request: Request):
    b = await request.json()
    cs = issues[key]["comments"]
    cs.append({"id": len(cs) + 1, "author": {"accountId": b.get("account", "human-1"), "displayName": b.get("name", "Rae")}, "text": b["text"], "created": str(time.time())})
    return {"id": len(cs)}


@app.get("/mock/issues/{key}")
def issue_state(key: str):
    return issues[key]


@app.get("/mock/issues")
def all_issues():
    return issues


@app.get("/mock/auth")
def auth_calls():
    return auth_log


@app.get("/mock/pulls")
def list_pulls():
    return pulls


@app.post("/mock/pulls/{number}/review")
async def add_review(number: int, request: Request):
    """A reviewer submits a review: {login, state (CHANGES_REQUESTED | APPROVED | COMMENTED), body}."""
    b = await request.json()
    rs = pulls[number - 1]["reviews"]
    rs.append({"id": 500 + len(rs), "user": {"login": b.get("login", "ann")}, "author_association": b.get("association", "MEMBER"), "state": b["state"], "body": b.get("body", ""), "submitted_at": f"{time.time():.6f}"})
    return rs[-1]


@app.post("/mock/pulls/{number}/fixup")
async def add_fixup(number: int, request: Request):
    body = await request.json()
    cs = pulls[number - 1].setdefault("commits", [])
    cs.append({"sha": f"fix{len(cs)}", "author": {"login": body.get("author", "rae")}, "commit": {"author": {"name": body.get("author", "rae")}, "message": body["message"]}, "parents": [{}]})
    return cs[-1]


@app.post("/mock/pulls/{number}/{action}")
def decide(number: int, action: str):
    """A person merges or closes the PR."""
    pulls[number - 1].update({"state": "closed", "merged": action == "merge", "merged_by": {"login": "rae"} if action == "merge" else None})
    return pulls[number - 1]


@app.post("/mock/checks/{owner}/{name}/{branch:path}")
async def set_checks(owner: str, name: str, branch: str, request: Request):
    """CI result for the branch's current head commit: {conclusion, title, summary}."""
    b = await request.json()
    sha = bare_rev(owner, name, branch)
    checks[sha] = [{"name": "tests", "status": "completed", "conclusion": b["conclusion"], "output": {"title": b.get("title", ""), "summary": b.get("summary", "")}}]
    return {"sha": sha}


@app.get("/mock/branch/{owner}/{name}/{branch:path}")
def branch_diff(owner: str, name: str, branch: str):
    """What a pushed branch changes relative to main, straight from the bare repository."""
    bare = REMOTES / owner / name
    run = lambda *a: subprocess.run(["git", "-c", "safe.directory=*", "--git-dir", str(bare), *a], capture_output=True, text=True)  # noqa: E731
    if run("rev-parse", "--verify", f"refs/heads/{branch}").returncode:
        return {"exists": False}
    return {"exists": True, "diff": run("diff", f"main...{branch}").stdout, "main_untouched": run("rev-parse", "main").stdout.strip()}


@app.post("/mock/llm-cost/{usd}")
def set_llm_cost(usd: float):
    llm_cost["usd"] = usd
    return llm_cost


@app.get("/healthz")
def healthz():
    return {"ok": True}
