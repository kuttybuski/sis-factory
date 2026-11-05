import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sandbox_provisioner.kube import KubeBackend, pod_manifest, pod_name
from sandbox_provisioner.models import SandboxInfo, SandboxSpec
from sandbox_provisioner.podman import PodmanBackend, create_payload

AUTH = {"Authorization": "Bearer internal-token"}
IMG = "localhost/sif-agent-runner:latest"


def spec(**kw):
    return SandboxSpec(task_id="PAY-1", image=IMG, env={"SIF_GATEWAY_URL": "http://sandbox-gateway:8080", "SIF_TASK_TOKEN": "t"}, **kw)


# ---- spec validation
def test_spec_rejects_unsafe_input():
    for bad in ("../x", "a b", "", "x" * 80, "-lead"):
        with pytest.raises(ValidationError):
            SandboxSpec(task_id=bad, image=IMG)
    with pytest.raises(ValidationError, match="SIF_"):
        SandboxSpec(task_id="PAY-1", image=IMG, env={"LD_PRELOAD": "/x.so"})
    with pytest.raises(ValidationError):
        SandboxSpec(task_id="PAY-1", image=IMG, memory_mb=999_999)


# ---- podman payload
def test_podman_payload_is_locked_down():
    p = create_payload(spec(), "sif-sandboxes", now=1000)
    assert p["cap_drop"] == ["ALL"] and p["no_new_privileges"] and p["read_only_filesystem"] and p["user"] == "10001:10001"
    assert p["Networks"] == {"sif-sandboxes": {}} and p["remove"] is True
    assert p["resource_limits"]["memory"]["limit"] == 1024 * 1024 * 1024 and p["resource_limits"]["pids"]["limit"] == 256
    assert {m["destination"] for m in p["mounts"]} == {"/workspace", "/tmp"} and all(m["type"] == "tmpfs" for m in p["mounts"])
    assert p["labels"]["sif.created"] == "1000" and "privileged" not in p and "oci_runtime" not in p
    assert create_payload(spec(), "n", runtime="runsc")["oci_runtime"] == "runsc"


def podman_transport(state):
    def handler(req: httpx.Request) -> httpx.Response:
        state["calls"].append((req.method, req.url.path))
        path = req.url.path
        if path.endswith("/exists"):
            return httpx.Response(204 if state["network"] else 404)
        if path.endswith("/networks/create"):
            state["network"] = True
            return httpx.Response(200, json={})
        if path.endswith("/containers/create"):
            state["created"] = json.loads(req.content)
            return httpx.Response(201, json={"Id": "abc"})
        if path.endswith("/start"):
            return httpx.Response(204)
        if req.method == "DELETE":
            return httpx.Response(200 if state.get("created") else 404)
        if path.endswith("/containers/json"):
            return httpx.Response(200, json=[{"Names": ["sif-sandbox-pay-1"], "State": "running", "Labels": state["created"]["labels"]}] if state.get("created") else [])
        return httpx.Response(500)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_podman_backend_creates_network_once_then_container_and_lists_it():
    state = {"calls": [], "network": False}
    b = PodmanBackend(httpx.AsyncClient(transport=podman_transport(state), base_url="http://p"), network="sif-sandboxes")
    info = await b.create(spec())
    assert info.name == "sif-sandbox-pay-1" and info.status == "running"
    assert [m for m, p in state["calls"] if p.endswith("/networks/create")] == ["POST"] and state["created"]["image"] == IMG
    assert [s.task_id for s in await b.list()] == ["PAY-1"] and (await b.get("PAY-1")).task_id == "PAY-1"
    assert await b.delete("PAY-1") is True


@pytest.mark.asyncio
async def test_podman_errors_surface_and_missing_deletes_are_quiet():
    b = PodmanBackend(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404 if r.method == "DELETE" else 500, text="boom")), base_url="http://p"))
    assert await b.delete("NOPE-1") is False
    with pytest.raises(RuntimeError, match="podman"):
        await b.create(spec())


# ---- kubernetes manifest
def test_pod_manifest_uses_gvisor_and_is_hardened():
    m = pod_manifest(spec(), "sif-sandboxes", now=5)
    s, c = m["spec"], m["spec"]["containers"][0]
    assert s["runtimeClassName"] == "gvisor" and s["automountServiceAccountToken"] is False and s["securityContext"]["runAsNonRoot"] is True
    assert c["securityContext"] == {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
    assert s["activeDeadlineSeconds"] == 14400 and m["metadata"]["namespace"] == "sif-sandboxes" and "hostPath" not in json.dumps(m)
    assert c["resources"]["limits"]["memory"] == "1024Mi" and {e["name"] for e in c["env"]} == {"SIF_GATEWAY_URL", "SIF_TASK_TOKEN"}


def test_pod_names_are_valid_dns_labels():
    assert pod_name("PAY-1") == "sandbox-pay-1" and pod_name("A_b.C") == "sandbox-a-b-c"


def test_manifest_satisfies_the_kyverno_policy_in_this_repo():
    # components/policy requires: gvisor runtime, non-root, no privilege escalation, no service-account token, no hostPath.
    m = pod_manifest(spec(), "sif-sandboxes")
    assert m["spec"]["runtimeClassName"] == "gvisor" and m["spec"]["automountServiceAccountToken"] is False
    assert m["spec"]["securityContext"]["runAsNonRoot"] is True and m["spec"]["containers"][0]["securityContext"]["allowPrivilegeEscalation"] is False


@pytest.mark.asyncio
async def test_kube_backend_talks_to_the_pods_api():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.method == "POST":
            return httpx.Response(201, json={})
        if req.method == "DELETE":
            return httpx.Response(200 if "sandbox-pay-1" in req.url.path else 404, json={})
        return httpx.Response(200, json={"items": [{"metadata": {"name": "sandbox-pay-1", "labels": {"sif.io/task": "PAY-1"}, "annotations": {"sif.io/created": "5", "sif.io/ttl": "60"}}, "status": {"phase": "Running"}}]})

    b = KubeBackend(httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://k"))
    assert (await b.create(spec())).backend == "kubernetes"
    assert ("POST", "/api/v1/namespaces/sif-sandboxes/pods") in seen
    assert (await b.list())[0].status == "running" and await b.delete("PAY-1") is True and await b.delete("NOPE-1") is False


# ---- API and reaper
class Fake:
    name = "fake"

    def __init__(self):
        self.items: dict[str, SandboxInfo] = {}

    async def create(self, s):
        self.items[s.task_id] = SandboxInfo(task_id=s.task_id, name="n", backend="fake", status="running", created_at=time.time(), ttl_seconds=s.ttl_seconds)
        return self.items[s.task_id]

    async def delete(self, t):
        return self.items.pop(t, None) is not None

    async def get(self, t):
        return self.items.get(t)

    async def list(self):
        return list(self.items.values())


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SIF_INTERNAL_TOKEN", "internal-token")
    monkeypatch.setenv("SIF_REAP_SECONDS", "0")
    import importlib

    from sandbox_provisioner import main

    importlib.reload(main)
    main.app.state.backend = Fake()
    with TestClient(main.app) as c:
        c.main = main
        yield c


def body(**kw):
    return {"task_id": "PAY-1", "image": IMG, "env": {"SIF_TASK_TOKEN": "t"}, **kw}


def test_api_needs_the_token(client, monkeypatch):
    assert client.post("/v1/sandboxes", json=body()).status_code == 401
    monkeypatch.delenv("SIF_INTERNAL_TOKEN")
    assert client.post("/v1/sandboxes", json=body(), headers=AUTH).status_code == 503


def test_api_creates_once_and_deletes_idempotently(client):
    assert client.post("/v1/sandboxes", json=body(), headers=AUTH).status_code == 200
    assert client.post("/v1/sandboxes", json=body(), headers=AUTH).status_code == 409
    assert client.get("/v1/sandboxes/PAY-1", headers=AUTH).json()["status"] == "running"
    assert client.delete("/v1/sandboxes/PAY-1", headers=AUTH).json() == {"deleted": True}
    assert client.delete("/v1/sandboxes/PAY-1", headers=AUTH).json() == {"deleted": False}
    assert client.get("/v1/sandboxes/PAY-1", headers=AUTH).status_code == 404


def test_only_allowlisted_images_run(client):
    assert client.post("/v1/sandboxes", json=body(image="docker.io/evil/miner:1"), headers=AUTH).status_code == 422
    assert client.post("/v1/sandboxes", json=body(image="localhost/sif-agent-runner-evil:1"), headers=AUTH).status_code == 422
    assert client.post("/v1/sandboxes", json=body(image="localhost/sif-agent-runner@sha256:abc"), headers=AUTH).status_code == 200


def test_unsafe_specs_are_rejected_by_the_api(client):
    assert client.post("/v1/sandboxes", json=body(env={"PATH": "/evil"}), headers=AUTH).status_code == 422
    assert client.post("/v1/sandboxes", json=body(task_id="../x"), headers=AUTH).status_code == 422


@pytest.mark.asyncio
async def test_reaper_destroys_only_expired_sandboxes(client):
    b = client.app.state.backend
    b.items["OLD-1"] = SandboxInfo(task_id="OLD-1", name="n", backend="fake", status="running", created_at=time.time() - 500, ttl_seconds=60)
    b.items["NEW-1"] = SandboxInfo(task_id="NEW-1", name="n", backend="fake", status="running", created_at=time.time(), ttl_seconds=3600)
    assert await client.main.reap(b) == ["OLD-1"] and list(b.items) == ["NEW-1"]


def test_podman_log_frames_are_unwrapped():
    from sandbox_provisioner.podman import demux

    frame = lambda stream, text: bytes([stream, 0, 0, 0]) + len(text).to_bytes(4, "big") + text  # noqa: E731
    assert demux(frame(1, b"started\n") + frame(2, b"Traceback\nRuntimeError: boom\n")) == "started\nTraceback\nRuntimeError: boom\n"
    assert demux(b"plain text from a tty\n") == "plain text from a tty\n"  # not framed: passed through


async def test_both_backends_read_the_last_lines_of_a_sandbox():
    seen = []

    def podman(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, dict(req.url.params)))
        if "sif-sandbox-gone" in req.url.path:
            return httpx.Response(404)
        return httpx.Response(200, content=bytes([1, 0, 0, 0, 0, 0, 0, 6]) + b"a\nb\nc\n")

    b = PodmanBackend(httpx.AsyncClient(transport=httpx.MockTransport(podman), base_url="http://p"))
    assert await b.logs("PAY-1", 2) == ["b", "c"] and seen[0][1] == {"stdout": "true", "stderr": "true", "tail": "2"}
    assert await b.logs("GONE", 2) is None

    k = KubeBackend(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="x\ny\n") if r.url.path.endswith("/log") else httpx.Response(404)),
                                      base_url="https://k"))
    assert await k.logs("PAY-1", 5) == ["x", "y"]
