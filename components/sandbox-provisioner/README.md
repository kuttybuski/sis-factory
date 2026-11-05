# sandbox-provisioner

Creates sandboxes; only holder of sandbox RBAC

Status: scaffold. See ../../docs/architecture.md.

`GET /v1/sandboxes/{task_id}/logs?tail=200` returns the last lines a sandbox printed. orchestrator-worker reads them just before it
deletes the sandbox and puts them on the task's log (source `sandbox`). On Kubernetes the provisioner's role therefore needs `get` on
`pods/log` in the sandbox namespace, next to `create`, `get`, `list` and `delete` on `pods`.
