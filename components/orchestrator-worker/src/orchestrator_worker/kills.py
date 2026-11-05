"""Turns dashboard kill requests into workflow signals."""

import logging

import httpx

from sis_contracts.namespaces import DEFAULT, Namespaces
from sis_contracts.workflow import SIG_KILL, workflow_id

log = logging.getLogger("orchestrator_worker")


async def forward_kill_requests(namespaces: Namespaces, portal: httpx.AsyncClient) -> int:
    r = await portal.get("/api/kill-requests/pending")
    r.raise_for_status()
    done = []
    for k in r.json():
        try:
            client = await namespaces.client(k.get("namespace") or DEFAULT)  # the workflow runs in its team's namespace
            await client.get_workflow_handle(workflow_id(k["task_id"])).signal(SIG_KILL, k["requested_by"])
        except Exception as e:  # the workflow already finished: the request is moot
            log.info("kill request for %s not delivered: %s", k["task_id"], e)
        done.append(k["id"])
    if done:
        (await portal.post("/api/kill-requests/handled", json={"ids": done})).raise_for_status()
    return len(done)
