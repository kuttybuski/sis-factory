"""The daily knowledge PR, end to end: portal-api decides which repositories are due and which lessons go in; this opens the PR through
git-broker, and later reads the decision back (merged, with which lines kept, or closed). Runs from the worker's request loop."""

import logging
from datetime import UTC, datetime

import httpx

from sif_contracts import digest

log = logging.getLogger("orchestrator_worker")


async def repo_file(broker: httpx.AsyncClient, repo: str, commit: str, path: str, team: str | None) -> str | None:
    r = await broker.post("/v1/file", json={"repo": repo, "commit": commit, "path": path, "team": team})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()["content"]


async def open_due_digests(portal: httpx.AsyncClient, broker: httpx.AsyncClient, path: str = "CLAUDE.md") -> int:
    r = await portal.post("/api/digests/claim")
    r.raise_for_status()
    opened = 0
    for d in r.json():
        repo, team, lessons = d["repo"], d.get("team"), d["lessons"]
        try:
            co = await broker.post("/v1/checkout", json={"repo": repo, "team": team})
            co.raise_for_status()
            base, ref = co.json()["commit"], co.json()["ref"]
            old = await repo_file(broker, repo, base, path, team)
            today = datetime.now(UTC).date()
            pr = await broker.post("/v1/publish", json={
                "repo": repo, "base_commit": base, "branch": f"sif/knowledge-{today.isoformat()}-{d['id']}", "patch": digest.file_patch(path, old, digest.append_lessons(old or "", lessons)),
                "title": digest.title(repo, len(lessons), today), "body": digest.pr_body(lessons, path), "base_branch": ref, "draft": False, "team": team})
            if pr.status_code >= 300:
                raise RuntimeError(f"git-broker {pr.status_code}: {pr.text[:300]}")
            (await portal.post(f"/api/digests/{d['id']}/opened", json={"pr_number": pr.json()["number"], "pr_url": pr.json()["pr_url"], "branch": pr.json()["branch"]})).raise_for_status()
            opened += 1
        except Exception as e:  # noqa: BLE001 - the lessons go back in the queue for the next attempt
            log.warning("knowledge PR for %s not opened: %s", repo, e)
            await portal.post(f"/api/digests/{d['id']}/decided", json={"state": "failed", "error": f"{type(e).__name__}: {e}"[:500]})
    return opened


async def check_open_digests(portal: httpx.AsyncClient, broker: httpx.AsyncClient, path: str = "CLAUDE.md") -> int:
    r = await portal.get("/api/digests", params={"status": "open"})
    r.raise_for_status()
    decided = 0
    for d in r.json():
        st = await broker.get(f"/v1/pr/{d['repo']}/{d['pr_number']}", params={k: v for k, v in (("team", d.get("team")),) if v})
        if st.status_code >= 300:
            log.info("could not read knowledge PR %s: %s", d["pr_url"], st.status_code)
            continue
        s = st.json()
        if s["state"] not in ("merged", "closed"):
            continue
        kept = sorted(digest.block_ids(await repo_file(broker, d["repo"], s["head_sha"], path, d.get("team")))) if s["state"] == "merged" else []
        (await portal.post(f"/api/digests/{d['id']}/decided", json={"state": s["state"], "merged_by": s.get("merged_by") or None, "kept_ids": kept})).raise_for_status()
        decided += 1
    return decided
