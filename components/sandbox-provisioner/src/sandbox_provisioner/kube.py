"""Cluster backend: one Pod per task with the gVisor RuntimeClass. Talks to the Kubernetes API with its own service account,
which should be allowed to create/get/list/delete pods in the sandbox namespace and nothing else (see the chart's Role)."""

import re
import time
from pathlib import Path

import httpx

from sandbox_provisioner.models import SandboxInfo, SandboxSpec

LABEL = "sis.io/managed"


def pod_name(task_id: str) -> str:
    return "sandbox-" + re.sub(r"[^a-z0-9-]", "-", task_id.lower()).strip("-")[:50]


def pod_manifest(spec: SandboxSpec, namespace: str, runtime_class: str = "gvisor", now: float | None = None) -> dict:
    mem = f"{spec.memory_mb}Mi"
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": pod_name(spec.task_id), "namespace": namespace,
                     "labels": {LABEL: "true", "sis.io/task": spec.task_id, "app.kubernetes.io/name": "sandbox"},
                     "annotations": {"sis.io/created": str(int(now or time.time())), "sis.io/ttl": str(spec.ttl_seconds)}},
        "spec": {
            "runtimeClassName": runtime_class, "restartPolicy": "Never", "automountServiceAccountToken": False, "enableServiceLinks": False,
            "activeDeadlineSeconds": spec.ttl_seconds,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "agent", "image": spec.image, "env": [{"name": k, "value": v} for k, v in spec.env.items()],
                "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                "resources": {"requests": {"cpu": "250m", "memory": mem}, "limits": {"cpu": str(spec.cpus), "memory": mem}},
                "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}, {"name": "tmp", "mountPath": "/tmp"}]}],
            "volumes": [{"name": "workspace", "emptyDir": {"sizeLimit": f"{spec.workspace_mb}Mi"}}, {"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}}]}}


class KubeBackend:
    name = "kubernetes"

    def __init__(self, client: httpx.AsyncClient | None = None, *, namespace: str = "sis-sandboxes", runtime_class: str = "gvisor") -> None:
        if client is None:
            token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
            client = httpx.AsyncClient(base_url="https://kubernetes.default.svc", headers={"Authorization": f"Bearer {token}"},
                                       verify="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt", timeout=30)
        self.client, self.ns, self.runtime_class = client, namespace, runtime_class

    @property
    def _pods(self) -> str:
        return f"/api/v1/namespaces/{self.ns}/pods"

    async def create(self, spec: SandboxSpec) -> SandboxInfo:
        manifest = pod_manifest(spec, self.ns, self.runtime_class)
        r = await self.client.post(self._pods, json=manifest)
        if r.status_code >= 300:
            raise RuntimeError(f"kubernetes: create pod failed ({r.status_code}): {r.text[:300]}")  # Kyverno denials show up here
        return SandboxInfo(task_id=spec.task_id, name=manifest["metadata"]["name"], backend=self.name, status="pending",
                           created_at=float(manifest["metadata"]["annotations"]["sis.io/created"]), ttl_seconds=spec.ttl_seconds)

    async def delete(self, task_id: str) -> bool:
        r = await self.client.delete(f"{self._pods}/{pod_name(task_id)}", params={"gracePeriodSeconds": "0"})
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True

    @staticmethod
    def _info(p: dict) -> SandboxInfo:
        m = p["metadata"]
        return SandboxInfo(task_id=m.get("labels", {}).get("sis.io/task", ""), name=m["name"], backend="kubernetes", status=p.get("status", {}).get("phase", "Unknown").lower(),
                           created_at=float(m.get("annotations", {}).get("sis.io/created", 0)), ttl_seconds=int(m.get("annotations", {}).get("sis.io/ttl", 0)))

    async def logs(self, task_id: str, tail: int) -> list[str] | None:
        """Needs `get` on pods/log in the sandbox namespace (the provisioner's role)."""
        r = await self.client.get(f"{self._pods}/{pod_name(task_id)}/log", params={"tailLines": str(tail)})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text.splitlines()

    async def list(self) -> list[SandboxInfo]:
        r = await self.client.get(self._pods, params={"labelSelector": f"{LABEL}=true"})
        r.raise_for_status()
        return [self._info(p) for p in r.json().get("items", [])]

    async def get(self, task_id: str) -> SandboxInfo | None:
        r = await self.client.get(f"{self._pods}/{pod_name(task_id)}")
        return None if r.status_code == 404 else self._info(r.json())
