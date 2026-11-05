"""Local backend: sibling containers through the Podman API. Isolation comes from an internal-only network (no route out),
dropped capabilities, a read-only root filesystem and resource limits. Add a gVisor runtime with SIF_PODMAN_RUNTIME=runsc."""

import json
import time

import httpx

from sandbox_provisioner.models import SandboxInfo, SandboxSpec

API = "/v4.0.0/libpod"
LABEL = "sif.managed"


def container_name(task_id: str) -> str:
    return f"sif-sandbox-{task_id.lower()}"


def create_payload(spec: SandboxSpec, network: str, runtime: str = "", now: float | None = None) -> dict:
    """The container definition. Verified against a real Podman 5 (capabilities dropped, read-only root, no route out)."""
    payload = {
        "name": container_name(spec.task_id), "image": spec.image, "env": spec.env,
        "labels": {LABEL: "true", "sif.task": spec.task_id, "sif.created": str(int(now or time.time())), "sif.ttl": str(spec.ttl_seconds)},
        "netns": {"nsmode": "bridge"}, "Networks": {network: {}},
        "cap_drop": ["ALL"], "no_new_privileges": True, "read_only_filesystem": True, "user": "10001:10001",
        "resource_limits": {"memory": {"limit": spec.memory_mb * 1024 * 1024}, "cpu": {"quota": int(spec.cpus * 100000), "period": 100000}, "pids": {"limit": spec.pids}},
        "mounts": [{"destination": "/workspace", "type": "tmpfs", "options": [f"size={spec.workspace_mb}m", "mode=1777"]},
                   {"destination": "/tmp", "type": "tmpfs", "options": ["size=256m", "mode=1777"]}],
        "remove": True,
    }
    if runtime:
        payload["oci_runtime"] = runtime
    return payload


def demux(data: bytes) -> str:
    """Podman's log stream for a container without a terminal: frames of an 8-byte header (stream, 0, 0, 0, length) and the bytes."""
    out, i = [], 0
    while i + 8 <= len(data) and data[i] in (0, 1, 2) and data[i + 1:i + 4] == b"\0\0\0":
        size = int.from_bytes(data[i + 4:i + 8], "big")
        out.append(data[i + 8:i + 8 + size])
        i += 8 + size
    return (b"".join(out) if out else data).decode("utf-8", "replace")


class PodmanBackend:
    name = "podman"

    def __init__(self, client: httpx.AsyncClient | None = None, *, socket: str = "/run/podman/podman.sock", network: str = "sif-sandboxes", runtime: str = "") -> None:
        self.client = client or httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket), base_url="http://podman", timeout=60)
        self.network, self.runtime = network, runtime

    async def _check(self, r: httpx.Response, what: str) -> httpx.Response:
        if r.status_code >= 300:
            raise RuntimeError(f"podman: {what} failed ({r.status_code}): {r.text[:300]}")
        return r

    async def create(self, spec: SandboxSpec) -> SandboxInfo:
        r = await self.client.get(f"{API}/networks/{self.network}/exists")
        if r.status_code == 404:
            await self._check(await self.client.post(f"{API}/networks/create", json={"name": self.network, "internal": True, "dns_enabled": True}), "create network")
        payload = create_payload(spec, self.network, self.runtime)
        await self._check(await self.client.post(f"{API}/containers/create", json=payload), "create container")
        await self._check(await self.client.post(f"{API}/containers/{payload['name']}/start"), "start container")
        return SandboxInfo(task_id=spec.task_id, name=payload["name"], backend=self.name, status="running", created_at=float(payload["labels"]["sif.created"]), ttl_seconds=spec.ttl_seconds)

    async def delete(self, task_id: str) -> bool:
        r = await self.client.delete(f"{API}/containers/{container_name(task_id)}", params={"force": "true"})
        if r.status_code == 404:
            return False
        await self._check(r, "delete container")
        return True

    @staticmethod
    def _info(c: dict) -> SandboxInfo:
        labels = c.get("Labels") or {}
        return SandboxInfo(task_id=labels.get("sif.task", ""), name=(c.get("Names") or [""])[0], backend="podman", status=c.get("State", "unknown"),
                           created_at=float(labels.get("sif.created", 0)), ttl_seconds=int(labels.get("sif.ttl", 0)))

    async def logs(self, task_id: str, tail: int) -> list[str] | None:
        r = await self.client.get(f"{API}/containers/{container_name(task_id)}/logs", params={"stdout": "true", "stderr": "true", "tail": str(tail)})
        if r.status_code == 404:
            return None
        await self._check(r, "read logs")
        return demux(r.content).splitlines()[-tail:]

    async def list(self) -> list[SandboxInfo]:
        r = await self._check(await self.client.get(f"{API}/containers/json", params={"all": "true", "filters": json.dumps({"label": [f"{LABEL}=true"]})}), "list containers")
        return [self._info(c) for c in r.json() or []]

    async def get(self, task_id: str) -> SandboxInfo | None:
        return next((s for s in await self.list() if s.task_id == task_id), None)
