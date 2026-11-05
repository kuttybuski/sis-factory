"""The security PR loop against fake portal-api and git-broker endpoints."""

import json
import subprocess

import httpx
import pytest
from injection_guard import Verdict, check_outbound, scan

from orchestrator_worker.security import BLOCK_HEAD, BLOCKLIST, LOG_FILE, check_open_security_prs, open_due_security_prs

pytestmark = pytest.mark.asyncio
BASE, HEAD = "b" * 40, "c" * 40
FA, FB = "a" * 64, "e" * 64


def incident(id: int, fp: str, **kw) -> dict:
    return {"id": id, "team": "Mobile", "repo": "acme/app", "jira_key": "SCRUM-7", "source": "jira_comment", "verdict": "block", "score": 85,
            "rules": ["override-instructions", "exfiltrate-secrets"], "fingerprint": fp, "actor": "ann@acme.com", "seen": 1,
            "created_at": "2026-11-01T10:00:00Z", **kw}


def git_apply(repo, patch: str) -> None:
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "p.diff").write_text(patch)
    subprocess.run(["git", "-C", str(repo), "apply", "p.diff"], check=True)


def fakes(files: dict[tuple[str, str], str], pr_state: dict | None = None, publish_status: int = 201):
    seen: list[tuple[str, str, dict]] = []

    def portal(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        seen.append(("portal", req.url.path, body))
        if req.url.path == "/api/security/claim":
            return httpx.Response(200, json=[{"repo": "acme/app", "team": "Mobile", "incidents": [incident(1, FA), incident(2, FB, verdict="flag", seen=3, actor="")]}])
        if req.url.path == "/api/security/incidents":
            return httpx.Response(200, json=[incident(1, FA, pr_number=4, pr_url="https://x/pull/4"), incident(2, FB, pr_number=4, pr_url="https://x/pull/4")])
        return httpx.Response(200, json={})

    def broker(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        seen.append(("broker", req.url.path, body))
        if req.url.path == "/v1/checkout":
            return httpx.Response(200, json={"commit": BASE, "ref": "main"})
        if req.url.path == "/v1/file":
            content = files.get((body["commit"], body["path"]))
            return httpx.Response(200, json={"content": content}) if content is not None else httpx.Response(404, json={})
        if req.url.path == "/v1/publish":
            return httpx.Response(publish_status, json={"number": 4, "pr_url": "https://x/pull/4", "branch": body["branch"]})
        if req.url.path.startswith("/v1/pr/"):
            return httpx.Response(200, json=pr_state)
        return httpx.Response(404)

    return (httpx.AsyncClient(base_url="http://portal", transport=httpx.MockTransport(portal)),
            httpx.AsyncClient(base_url="http://broker", transport=httpx.MockTransport(broker)), seen)


async def test_new_attempts_become_one_pr_on_the_team_repo_with_a_log_and_a_blocklist(tmp_path):
    portal, broker, seen = fakes({})
    assert await open_due_security_prs(portal, broker) == 1
    pub = next(b for _, path, b in seen if path == "/v1/publish")
    assert pub["repo"] == "acme/app" and pub["team"] == "Mobile" and pub["branch"].startswith("sis/security-") and pub["branch"].endswith("-1") and not pub["draft"]
    assert pub["title"].startswith("Security: 2 prompt-injection attempts caught")
    assert "| SCRUM-7 | a reply on Jira or in the 3D view | block | 85 |" in pub["body"] and "**Merge**" in pub["body"]
    patch = pub["patch"]
    assert f"+++ b/{LOG_FILE}" in patch and f"+++ b/{BLOCKLIST}" in patch and patch.count("new file mode 100644") == 2
    assert "SCRUM-7 · blocked" in patch and "by ann@acme.com" in patch and "seen 3 times" in patch and f"+{FA}  # SCRUM-7 2026-11-01" in patch
    assert ("portal", "/api/security/opened", {"ids": [1, 2], "pr_number": 4, "pr_url": "https://x/pull/4", "branch": pub["branch"]}) in seen

    # git-broker screens everything it publishes: the factory's own security PR must get through, so it never carries the attack text.
    for text in (pub["title"], pub["body"], patch):
        assert check_outbound(text).ok and scan(text, "pr_description").verdict is not Verdict.BLOCK
    git_apply(tmp_path, patch)
    assert (tmp_path / BLOCKLIST).read_text().startswith(BLOCK_HEAD)


async def test_a_fingerprint_already_in_the_blocklist_is_not_added_twice():
    existing = BLOCK_HEAD + f"{FA}  # SCRUM-1 2026-10-01 override-instructions\n"
    portal, broker, seen = fakes({(BASE, BLOCKLIST): existing, (BASE, LOG_FILE): "# Prompt-injection attempts\n"})
    await open_due_security_prs(portal, broker)
    patch = next(b for _, path, b in seen if path == "/v1/publish")["patch"]
    assert f"+{FB}" in patch and f"+{FA}" not in patch and "new file mode" not in patch


async def test_a_pr_that_cannot_be_opened_is_marked_failed_to_retry_later():
    portal, broker, seen = fakes({}, publish_status=422)
    assert await open_due_security_prs(portal, broker) == 0
    failed = next(b for who, path, b in seen if path == "/api/security/decided")
    assert failed["ids"] == [1, 2] and failed["state"] == "failed" and "422" in failed["error"]


async def test_merging_blocks_what_is_still_listed_and_a_deleted_line_means_harmless():
    portal, broker, seen = fakes({(HEAD, BLOCKLIST): BLOCK_HEAD + f"{FA}  # SCRUM-7\n"}, pr_state={"state": "merged", "head_sha": HEAD, "merged_by": "rae"})
    assert await check_open_security_prs(portal, broker) == 1
    decided = [b for _, path, b in seen if path == "/api/security/decided"]
    assert decided == [{"ids": [1], "state": "merged", "by": "rae"}, {"ids": [2], "state": "closed", "by": "rae"}]


async def test_a_closed_pr_marks_every_attempt_harmless_and_an_open_one_waits():
    portal, broker, seen = fakes({}, pr_state={"state": "closed", "head_sha": HEAD, "merged_by": None})
    await check_open_security_prs(portal, broker)
    assert [b for _, path, b in seen if path == "/api/security/decided"] == [{"ids": [1, 2], "state": "closed", "by": None}]
    portal, broker, seen = fakes({}, pr_state={"state": "open", "head_sha": HEAD})
    assert await check_open_security_prs(portal, broker) == 0
