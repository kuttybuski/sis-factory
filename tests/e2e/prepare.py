"""Builds the end-to-end compose file: base + overlay merged, no published ports, its own sandbox network. Runs in a container (needs PyYAML)."""

import sys

import yaml


def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: merge(a[k], b[k]) if k in a and k in b else b.get(k, a.get(k)) for k in {**a, **b}}
    if isinstance(a, list) and isinstance(b, list):
        return a + [x for x in b if x not in a]
    return b


base, overlay = (yaml.safe_load(open(f)) for f in sys.argv[1:3])
c = merge(base, overlay)
for svc in c["services"].values():
    svc.pop("ports", None)  # never collide with a running dev stack
    if "environment" in svc and isinstance(svc["environment"], dict) and "SIS_SANDBOX_NETWORK" in svc["environment"]:
        svc["environment"]["SIS_SANDBOX_NETWORK"] = "sise2e-sandboxes"
c["networks"]["sandboxes"]["name"] = "sise2e-sandboxes"
c["services"]["embeddings"].setdefault("environment", {})["EMBEDDINGS_BACKEND"] = "hash"  # the model download is not part of this test
c["services"]["3d-view"]["profiles"] = ["never"]  # not needed here
c["services"]["portal-web"]["profiles"] = ["never"]
for name in ("3d-view", "portal-web"):
    for svc in c["services"].values():
        if isinstance(svc.get("depends_on"), list) and name in svc["depends_on"]:
            svc["depends_on"].remove(name)
yaml.safe_dump(c, open(sys.argv[3], "w"), sort_keys=False)
