"""Starts the replay runs people asked for on the dashboard (or the improvement cycle asked for), the same way kill requests are forwarded."""

import logging

import httpx

from sis_contracts.namespaces import DEFAULT, Namespaces
from sis_contracts.workflow import EVAL_WORKFLOW, ORCHESTRATOR_QUEUE, EvalCase, EvalInput

log = logging.getLogger("orchestrator_worker")


async def start_requested_evals(namespaces: Namespaces, portal: httpx.AsyncClient) -> int:
    r = await portal.post("/api/eval/runs/claim")
    r.raise_for_status()
    started = 0
    for run in r.json():
        e = EvalInput(run_id=run["id"], lesson_id=run["lesson_id"], cases=[EvalCase(**c) for c in run["cases"]], budget_usd=run["budget_usd"],
                      pass_score=run["pass_score"])
        try:
            client = await namespaces.client(DEFAULT)
            await client.start_workflow(EVAL_WORKFLOW, e, id=f"eval-run-{run['id']}", task_queue=ORCHESTRATOR_QUEUE)
            started += 1
        except Exception as err:  # noqa: BLE001 - report it on the run so the dashboard shows why
            log.warning("could not start replay run %s: %s", run["id"], err)
            await portal.post(f"/api/eval/runs/{run['id']}/result", json={"error": f"could not start: {err}"[:500]})
    return started
