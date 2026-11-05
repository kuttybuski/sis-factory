"""The security PR, end to end: every prompt-injection attempt the factory caught is recorded in the team's own repository. portal-api
decides which repositories are due (new attempts, no security PR open); this opens one PR per repository through git-broker, and later
reads the decision back. Merged: the fingerprints still in the blocklist are blocked for the team; ones a reviewer deleted, or a closed
PR, mean people judged them harmless. The attempt's text is never written anywhere: the Jira key leads to it. Runs from the worker's loop."""

import logging
from datetime import UTC, datetime

import httpx

from sif_contracts import digest

from orchestrator_worker.digests import repo_file

log = logging.getLogger("orchestrator_worker")

LOG_FILE = ".sif/security/injection-log.md"
BLOCKLIST = ".sif/security/blocklist.txt"
LOG_HEAD = ("# Prompt-injection attempts\n\nText the SIF factory did not pass to its agents because it looked like an attempt to instruct them. "
            "The text is not copied here; follow the Jira key to read it.\n")
BLOCK_HEAD = ("# Fingerprints the SIF factory always blocks for this team, whatever its rules score. One per line, from merged security PRs.\n"
              "# Delete a line (in a PR) to stop blocking it.\n")
VERDICT = {"block": "blocked", "flag": "flagged"}
WHERE = {"jira_ticket": "ticket text", "jira_comment": "a reply on Jira or in the 3D view", "pr_review": "a pull-request review comment"}


def log_entry(i: dict) -> str:
    when = str(i["created_at"])[:10]
    rules = ", ".join(i["rules"]) or "none"
    by = f" by {i['actor']}" if i["actor"] else ""
    times = f"; seen {i['seen']} times" if i["seen"] > 1 else ""
    return (f"\n## {when} · {i['jira_key']} · {VERDICT[i['verdict']]}\n\n- Where: {WHERE.get(i['source'], i['source'])}{by}\n- Score {i['score']}; rules: {rules}{times}\n"
            f"- Fingerprint: `{i['fingerprint']}`\n")


def block_line(i: dict) -> str:
    return f"{i['fingerprint']}  # {i['jira_key']} {str(i['created_at'])[:10]} {','.join(i['rules'][:3])}\n"


def fingerprints(text: str | None) -> set[str]:
    return {line.split()[0] for line in (text or "").splitlines() if line.strip() and not line.startswith("#")}


def pr_body(team: str | None, incidents: list[dict]) -> str:
    rows = "\n".join(f"| {i['jira_key']} | {WHERE.get(i['source'], i['source'])} | {i['verdict']} | {i['score']} | {', '.join(i['rules'][:3]) or '-'} |" for i in incidents)
    return (f"The factory caught {len(incidents)} prompt-injection attempt{'s' if len(incidents) != 1 else ''} on work for "
            f"{'team ' + team if team else 'this repository'}. None of it reached an agent unchecked: blocked text was held back, flagged text "
            f"was passed on marked as untrusted.\n\n| Jira | Where | Verdict | Score | Rules |\n|---|---|---|---|---|\n{rows}\n\n"
            f"**Merge** to block these fingerprints for the team from now on, even where the rules alone would let the same text through. "
            f"**Delete a line** from `{BLOCKLIST}` first to leave that one unblocked, or **close** the PR if they were all harmless. "
            f"The attempts are logged in `{LOG_FILE}`; the text stays in Jira.")


async def open_due_security_prs(portal: httpx.AsyncClient, broker: httpx.AsyncClient) -> int:
    r = await portal.post("/api/security/claim")
    r.raise_for_status()
    opened = 0
    for g in r.json():
        repo, team, incidents = g["repo"], g.get("team"), g["incidents"]
        ids = [i["id"] for i in incidents]
        try:
            co = await broker.post("/v1/checkout", json={"repo": repo, "team": team})
            co.raise_for_status()
            base, ref = co.json()["commit"], co.json()["ref"]
            old_log, old_block = await repo_file(broker, repo, base, LOG_FILE, team), await repo_file(broker, repo, base, BLOCKLIST, team)
            known = fingerprints(old_block)
            patch = (digest.file_patch(LOG_FILE, old_log, (old_log or LOG_HEAD) + "".join(log_entry(i) for i in incidents))
                     + digest.file_patch(BLOCKLIST, old_block, (old_block or BLOCK_HEAD) + "".join(block_line(i) for i in incidents if i["fingerprint"] not in known)))
            today = datetime.now(UTC).date()
            pr = await broker.post("/v1/publish", json={
                "repo": repo, "base_commit": base, "branch": f"sif/security-{today.isoformat()}-{ids[0]}", "patch": patch,
                "title": f"Security: {len(incidents)} prompt-injection attempt{'s' if len(incidents) != 1 else ''} caught ({today.isoformat()})",
                "body": pr_body(team, incidents), "base_branch": ref, "draft": False, "team": team})
            if pr.status_code >= 300:
                raise RuntimeError(f"git-broker {pr.status_code}: {pr.text[:300]}")
            p = pr.json()
            (await portal.post("/api/security/opened", json={"ids": ids, "pr_number": p["number"], "pr_url": p["pr_url"], "branch": p["branch"]})).raise_for_status()
            log.warning("security PR for %s opened: %s (%d attempts)", repo, p["pr_url"], len(incidents))
            opened += 1
        except Exception as e:  # noqa: BLE001 - the attempts stay recorded and are tried again later
            log.warning("security PR for %s not opened: %s", repo, e)
            await portal.post("/api/security/decided", json={"ids": ids, "state": "failed", "error": f"{type(e).__name__}: {e}"[:500]})
    return opened


async def check_open_security_prs(portal: httpx.AsyncClient, broker: httpx.AsyncClient) -> int:
    r = await portal.get("/api/security/incidents", params={"status": "open"})
    r.raise_for_status()
    prs: dict[tuple[str, int], list[dict]] = {}
    for i in r.json():
        prs.setdefault((i["repo"], i["pr_number"]), []).append(i)
    decided = 0
    for (repo, number), incidents in prs.items():
        team = incidents[0]["team"] or None
        st = await broker.get(f"/v1/pr/{repo}/{number}", params={"team": team} if team else {})
        if st.status_code >= 300:
            log.info("could not read security PR %s: %s", incidents[0]["pr_url"], st.status_code)
            continue
        s = st.json()
        if s["state"] not in ("merged", "closed"):
            continue
        kept = fingerprints(await repo_file(broker, repo, s["head_sha"], BLOCKLIST, team)) if s["state"] == "merged" else set()
        merged = [i["id"] for i in incidents if i["fingerprint"] in kept]
        closed = [i["id"] for i in incidents if i["fingerprint"] not in kept]
        for state, ids in (("merged", merged), ("closed", closed)):
            if ids:
                (await portal.post("/api/security/decided", json={"ids": ids, "state": state, "by": s.get("merged_by") or None})).raise_for_status()
        decided += 1
    return decided
