import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from temporalio.worker import Worker

from sis_contracts import posture
from sis_contracts import Settings
from sis_contracts.namespaces import NamespaceWorkers, Namespaces, team_namespaces
from sis_contracts.workflow import ORCHESTRATOR_QUEUE

from orchestrator_worker import activities
from orchestrator_worker.digests import check_open_digests, open_due_digests
from orchestrator_worker.evals import start_requested_evals
from orchestrator_worker.kills import forward_kill_requests
from orchestrator_worker.security import check_open_security_prs, open_due_security_prs
from orchestrator_worker.workflow import EvalWorkflow, ReplayWorkflow, ReviewWorkflow, TaskWorkflow

log = logging.getLogger("orchestrator_worker")


async def request_loop(namespaces: Namespaces, deps: activities.Deps, every: float) -> None:
    while True:
        await asyncio.sleep(every)
        for what, step in (("kill", forward_kill_requests), ("replay", start_requested_evals)):
            try:
                await step(namespaces, deps.portal)
            except Exception as e:
                log.warning("%s poll failed: %s: %s", what, type(e).__name__, e)  # some errors (a refused connection at startup) have no message


async def digest_loop(deps: activities.Deps, every: float) -> None:
    """The PRs the factory opens on team repositories, the daily knowledge PR and the security PR: open the ones that are due, read back
    the ones people decided. portal-api decides what is due."""
    p, b, f = deps.portal, deps.broker, deps.settings.digest_file
    while True:
        await asyncio.sleep(every)
        for what, step in (("knowledge PR", lambda: open_due_digests(p, b, f)), ("knowledge PR check", lambda: check_open_digests(p, b, f)),
                           ("security PR", lambda: open_due_security_prs(p, b)), ("security PR check", lambda: check_open_security_prs(p, b))):
            try:
                await step()
            except Exception as e:
                log.warning("%s failed: %s: %s", what, type(e).__name__, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    posture.enforce("orchestrator-worker")  # SIS_ENV=production: refuse to start with unsafe settings (sis_contracts.posture)
    deps = activities.internal_clients()
    activities.set_deps(deps)
    s = Settings()
    namespaces = Namespaces(os.environ.get("TEMPORAL_ADDRESS", "temporal:7233"), s.namespace_retention_days)
    # One worker per team namespace; teams added on the dashboard are picked up on the next sync.
    workers = NamespaceWorkers(namespaces, lambda c: Worker(c, task_queue=ORCHESTRATOR_QUEUE, workflows=[TaskWorkflow, ReviewWorkflow, ReplayWorkflow, EvalWorkflow], activities=activities.ALL),
                               lambda: team_namespaces(deps.portal), every=s.namespace_refresh_seconds)
    await workers.sync()
    tasks = [asyncio.create_task(workers.run()), asyncio.create_task(request_loop(namespaces, deps, float(os.environ.get("SIS_KILL_POLL_SECONDS", "5")))),
             asyncio.create_task(digest_loop(deps, float(os.environ.get("SIS_DIGEST_POLL_SECONDS", "300"))))]
    app.state.tasks, app.state.workers = tasks, workers
    yield
    for t in tasks:
        t.cancel()
    await workers.stop()


app = FastAPI(title="sis orchestrator-worker", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    dead = [t for t in app.state.tasks if t.done()]
    return {"ok": not dead and app.state.workers.healthy(), "namespaces": app.state.workers.status()}
