# Roadmap: production deployment pieces

What runs today is the Compose stack (`compose.yaml`): every service listed there has code and tests. The pieces below are planned for a
Kubernetes deployment and have no code yet; they used to be empty folders under `components/`.

| Piece | Plan |
|---|---|
| temporal | Temporal server and UI, self-hosted (MIT). Today: the `temporal` dev server in Compose. |
| postgres | CloudNativePG operator and the Temporal database cluster. Today: one Postgres container. |
| secrets | External Secrets Operator, so tokens come from the company vault instead of `.env`. |
| llm-gateway | LiteLLM proxy for model keys, budgets and routing. Today: sandbox-gateway forwards to `LLM_UPSTREAM_URL`. |
| gateway-api | Envoy Gateway (Gateway API ingress) in front of the dashboard and auth-proxy. |
| gitops | Argo CD app-of-apps for the charts under `components/*/chart`. |
| otel-collector, metrics, dashboards | OpenTelemetry Collector, Prometheus, Grafana and Loki. |
| env-builder | Builds per-repository sandbox images from internal mirrors, so agents can run each repo's own tests. |
| agent-sandbox | kubernetes-sigs/agent-sandbox CRDs and controller, replacing the Podman-based sandbox-provisioner at scale. |

Kubernetes manifests that already exist: `components/sandbox-runtime/chart` (gVisor RuntimeClass), `components/policy/chart` (sandbox
baseline policy) and `components/network-policy/chart` (default-deny with a path to the gateway only); `make lint-charts` checks them.
