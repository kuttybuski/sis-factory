# Tests

All tests live here, with one subfolder per module. Nothing is tested from inside `components/`.

| Folder | Covers |
|---|---|
| `tests/<component>/` | Unit and integration tests for `components/<component>/` (pytest for Python, Vitest for `portal-web`, `helm-unittest`/`kubeconform` for charts, Kyverno Chainsaw for `policy`) |
| `tests/libs-contracts/` | Shared Pydantic/OpenAPI contract tests |
| `tests/evals/` | Agent and LLM evals with Inspect AI (scorers, golden sets, regression on model or prompt changes) |
| `tests/e2e/` | Cluster end-to-end tests (kind/k3d, mock Jira, sandbox isolation) |

Defaults: evals use the lowest model tier; override via `.env` (`MODEL_IMPLEMENTATION`, `LLM_UPSTREAM_*`).
