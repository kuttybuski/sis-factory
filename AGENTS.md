# Notes for AI coding agents

SIF is a software factory: it picks up Jira tickets, has a coding agent change the code in a locked-down sandbox, and opens a draft pull
request for people to review. Read this before changing anything. People setting up the app should read docs/running.md instead.

## Layout

- `components/*`: one service per folder. Python services (uv workspace, FastAPI, Temporal) and two front ends: `portal-web` (Next.js
  dashboard) and `3d-view` (Vite, React, react-three-fiber).
- `libs/contracts` (shared types, settings, workflow names, production checks), `libs/context` (context index and search).
- `tests/<component>/`: tests live here, not next to the code. `tests/3d-view/visual/` holds the screenshot tests and their PNGs.
- `docs/`: how it works and how to run it. Update the matching page when behaviour changes.

## Commands

Everything runs in containers through `make`; the host only needs Podman. Do not install Python or Node packages on the host.

| Before you say you are done | Command |
|---|---|
| Python changes | `make test-py` (starts throwaway Postgres and Temporal; about two minutes) |
| 3D view changes | `make test-3d` and `make build-3d`; `make test-3d-visual` when anything visible may have changed |
| Dashboard changes | `podman build --target build -f components/portal-web/Dockerfile components/portal-web` (type-checks and builds) |
| Helm chart changes | `make lint-charts` |
| A single Python test file, quickly | `uv run pytest -q tests/<component>/test_x.py`, if uv is on the host; only for tests that need no database or Temporal |

Run what applies, and report failures with their output instead of working around them. `make test-3d-visual UPDATE=1` rewrites the
reference screenshots: only for an intended visual change, and look at every changed PNG before keeping it.

## Do not touch what is live

A developer's `.env` can hold real Jira, GitHub and model credentials, and `make up` runs against them.

- Do not edit `.env`, and do not run `make init` against it, unless the person asks. Show the change instead.
- Do not turn off dry run (`SIF_DRY_RUN`), switch the agent (`SIF_AGENT`), or set `SIF_ENV=production` on someone's stack unasked.
- Do not create Jira issues, comments or labels, GitHub branches or PRs, teams, connections or lessons on a live stack to test something.
  Read-only calls are fine. Test writes against the fakes in `tests/` or the e2e mocks (`make e2e`).
- Browser checks of the 3D view use made-up data (Playwright route interception or a fake EventSource), as in `tests/3d-view/visual`.
- Do not commit or push unless the person asks.

## Rules the code depends on

- **Temporal workflows replay.** `components/orchestrator-worker/src/orchestrator_worker/workflow.py` must stay deterministic: no I/O,
  clocks or randomness outside activities (`workflow.now()` is fine). A change that adds, removes or reorders activity calls, timers or
  signals breaks tasks that are already running unless it is guarded with `workflow.patched("<id>")` (see `INCIDENTS` there).
  Changing an activity's arguments or return value is safe.
- **Untrusted text.** Ticket text, Jira comments, answers from the 3D view, PR comments and tool output can carry prompt injection. Text
  that reaches an agent goes through `TaskWorkflow.screen()` (injection-guard, with incident reporting). Everything published to GitHub
  passes git-broker's `screen()`. Do not add a path around either.
- **Jira comments are for people.** Post with `self.jira(...)` only for what someone must act on or for an outcome; progress goes to
  `self.note(...)` (the task's log, the 3D view and the Temporal `logs` query).
- **Production checks.** `libs/contracts/src/sif_contracts/posture.py` makes services refuse to start with unsafe settings when
  `SIF_ENV=production`. A new security-relevant setting gets a check there and a line in docs/security.md.
- **SQL.** portal-api uses raw SQL through psycopg. Interpolate only fixed strings; every value is a parameter (`%s`).
- **Settings that reach the browser.** The 3D view gets an explicit allowlist from `vite.config.ts`. Never widen it to a prefix:
  `SIF_` also covers the tokens.

## Style

Match the code around you: short functions, comments that say why rather than what, names over abbreviations, and the comment density
of the file you are in. Python lines go up to about 150 characters. User-facing text is plain English without jargon. Keep a change to
what was asked; mention other problems you notice instead of fixing them unasked.
