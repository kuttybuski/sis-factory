import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from sandbox_provisioner.models import Backend, SandboxInfo, SandboxSpec

log = logging.getLogger("sandbox_provisioner")
REAP_SECONDS = int(os.environ.get("SIS_REAP_SECONDS", "60"))


def make_backend() -> Backend:
    kind = os.environ.get("SIS_SANDBOX_BACKEND", "podman")
    if kind == "kubernetes":
        from sandbox_provisioner.kube import KubeBackend
        return KubeBackend(namespace=os.environ.get("SIS_SANDBOX_NAMESPACE", "sis-sandboxes"), runtime_class=os.environ.get("SIS_RUNTIME_CLASS", "gvisor"))
    from sandbox_provisioner.podman import PodmanBackend
    return PodmanBackend(network=os.environ.get("SIS_SANDBOX_NETWORK", "sis-sandboxes"), runtime=os.environ.get("SIS_PODMAN_RUNTIME", ""))


async def reap(backend: Backend) -> list[str]:
    """Destroys sandboxes past their time limit, whatever the orchestrator thinks. A crashed workflow must not leak a running agent."""
    gone = []
    for s in await backend.list():
        if s.task_id and s.expired():
            await backend.delete(s.task_id)
            gone.append(s.task_id)
    return gone


async def reaper(backend: Backend):
    while True:
        await asyncio.sleep(REAP_SECONDS)
        try:
            if gone := await reap(backend):
                log.warning("reaped expired sandboxes: %s", gone)
        except Exception:
            log.exception("reaper failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.environ.get("SIS_ENV", "").lower() == "production" and os.environ.get("SIS_SANDBOX_BACKEND", "podman") == "podman" and not os.environ.get("SIS_PODMAN_RUNTIME"):
        raise SystemExit("sandbox-provisioner will not start with SIS_ENV=production: Podman sandboxes need a gVisor runtime (SIS_PODMAN_RUNTIME=runsc), "
                         "or use SIS_SANDBOX_BACKEND=kubernetes (sis_contracts.posture)")
    app.state.backend = getattr(app.state, "backend", None) or make_backend()
    task = asyncio.create_task(reaper(app.state.backend)) if REAP_SECONDS > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="sis sandbox-provisioner", lifespan=lifespan)


def internal(authorization: str | None = Header(default=None)) -> None:
    token = os.environ.get("SIS_INTERNAL_TOKEN", "")
    if not token:
        raise HTTPException(503, "SIS_INTERNAL_TOKEN is not configured")
    if not secrets.compare_digest((authorization or "").removeprefix("Bearer "), token):
        raise HTTPException(401, "invalid token")


def allowed_image(image: str) -> bool:
    prefixes = [p for p in os.environ.get("SIS_SANDBOX_IMAGE_ALLOW", "localhost/sis-agent-runner").split(",") if p]
    return any(image == p or image.startswith((p + ":", p + "@")) for p in prefixes)


@app.get("/healthz")
def healthz():
    return {"ok": True, "backend": app.state.backend.name}


@app.post("/v1/sandboxes", response_model=SandboxInfo, dependencies=[Depends(internal)])
async def create(spec: SandboxSpec):
    if not allowed_image(spec.image):
        raise HTTPException(422, "image is not on the allowlist (SIS_SANDBOX_IMAGE_ALLOW)")  # a compromised caller cannot run arbitrary images
    if await app.state.backend.get(spec.task_id):
        raise HTTPException(409, "a sandbox for this task already exists")
    try:
        return await app.state.backend.create(spec)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.get("/v1/sandboxes", dependencies=[Depends(internal)])
async def list_all():
    return await app.state.backend.list()


@app.get("/v1/sandboxes/{task_id}", response_model=SandboxInfo, dependencies=[Depends(internal)])
async def get(task_id: str):
    info = await app.state.backend.get(task_id)
    if info is None:
        raise HTTPException(404, "no such sandbox")
    return info


@app.get("/v1/sandboxes/{task_id}/logs", dependencies=[Depends(internal)])
async def logs(task_id: str, tail: int = Query(200, ge=1, le=2000)):
    """The last lines of the sandbox's own output (stdout and stderr), read before it is deleted: what happened inside, crashes included."""
    lines = await app.state.backend.logs(task_id, tail)
    if lines is None:
        raise HTTPException(404, "no such sandbox")
    return {"lines": lines}


@app.delete("/v1/sandboxes/{task_id}", dependencies=[Depends(internal)])
async def delete(task_id: str):
    return {"deleted": await app.state.backend.delete(task_id)}  # idempotent: deleting a missing sandbox is not an error
