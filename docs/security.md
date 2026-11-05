# Security model

What is protected, from whom and what is still open: docs/threat-model.md.

| Who | Can | Holds |
|---|---|---|
| A browser on this machine | read the dashboard, 3D view, API docs | nothing secret |
| The dashboard server (portal-web) | every write, through its forms (server actions, origin-checked by Next.js) | `SIF_DASHBOARD_TOKEN` |
| jira-connector, orchestrator-worker, sandbox-gateway, context-ingest | post events, act on kills, propose lessons; never people's decisions | `SIF_INGEST_TOKEN` |
| jira-connector, git-broker, context-ingest | read a team's decrypted Jira / GitHub credentials | `SIF_INTERNAL_TOKEN` |
| portal-api | encrypt and decrypt those credentials | `SIF_SECRET_KEY` |
| A sandbox (agent) | talk to sandbox-gateway only (`sif-sandboxes` is internal: no internet, no other service) | a per-task token |

Rules the code keeps:

- **Every portal-api write needs a token** (`portal_api/auth.py`), including kill, handling policies and lesson approval. A web page cannot
  call them through the user's browser. People's decisions (approving lessons, teams, team knowledge, handling, connections, kills, pausing
  intake) need the dashboard's token: a service token gets 403 there, so a compromised service cannot approve its own lessons.
- **Only named settings reach the browser.** The 3D view gets an allowlist from `vite.config.ts`; never widen it to a prefix, since `SIF_`
  also covers the tokens and the key.
- **The Temporal UI is read-only** behind the dashboard (`portal-web/proxy.ts`); stopping work goes through Kill task, which cleans up.
- **Ports are bound to 127.0.0.1.** Postgres, Temporal's gRPC port and the internal services are not published at all.
- **The sandbox provisioner holds the Podman socket** and is on the default network only, so no sandbox can reach it.
- **Dashboard pages cannot be framed by other sites** (`X-Frame-Options: DENY`, CSP `frame-ancestors 'none'`).

## Sign-in, roles and audit

Set `SIF_AUTH_MODE=proxy` and run the `sso` profile (`components/auth-proxy/README.md`): oauth2-proxy signs people in with the company's OIDC
provider and passes their email and groups to portal-web, which refuses any request that did not come through it. Remove portal-web's
published port so the proxy is the only way in (headers alone can be forged). Roles come from `SIF_PORTAL_ADMINS` and `SIF_PORTAL_OPERATORS`
(emails or groups): viewers read; operators kill tasks, review lessons and start syncs; admins also change teams, connections and handling.
Every write through portal-api is recorded in the audit log (Admin → Audit log) with the person's email, including refused attempts.
Approvals and answers given on Jira and GitHub are recorded on each task's trail with who gave them. Without `SIF_AUTH_MODE=proxy` the
header says "No login (local)": do not share the dashboard that way.

Team leads: a team can list leads (emails or IdP groups) on the Teams page. Its knowledge, its lessons and its tasks are then theirs: the
global operators can no longer edit, approve or kill there, admins still can. A team without leads is run by the global operators.

## What the factory may change on GitHub

git-broker is the only component that writes to GitHub, and it checks every patch before pushing (`git_broker/gitops.py`):

- Only `sif/*` branches, never a force-push, and it has no merge endpoint.
- No protected paths: CI definitions (they run with the repository's secrets) and `CODEOWNERS` (it decides who must review).
- No dependency changes (manifests and lockfiles) unless `SIF_ALLOW_DEPENDENCY_CHANGES`; a person adds packages.
- No more than `SIF_MAX_CHANGED_FILES` files or `SIF_MAX_CHANGED_LINES` lines; larger work is split into smaller tickets.
- No secrets or prompt-injection text in the patch, title or body.
- With `SIF_REQUIRE_PROTECTED_BRANCH` (on in production) no PR onto a branch that does not require an approving review. That is what
  guarantees a person reviews before anything merges, whatever the token could do.

PR feedback only counts from people in the organisation (GitHub `author_association` OWNER, MEMBER or COLLABORATOR): on a public repository
anyone can comment, and feedback becomes the agent's next instructions and, if it recurs, a lesson. Review mining in context-ingest uses the same rule.

**Use a GitHub App, not a personal token.** Create an app with Contents, Pull requests and Issues read and write, and Checks, Commit statuses
and Metadata read. Do not give it Administration, Workflows or Secrets. Install it only on the factory's repositories, then set
`GITHUB_APP_ID` and `GITHUB_APP_PRIVATE_KEY` (or `GITHUB_APP_PRIVATE_KEY_FILE`). git-broker mints a token per repository, scoped to that
repository and those permissions, and it expires within the hour. Then protect each default branch with a ruleset or branch protection
that requires at least one approving review. The Teams page shows, per repository, which identity is used and whether it is safe.

## Running in production

Set `SIF_ENV=production`. Each service then refuses to start if something below is missing (`sif_contracts/posture.py`), and the dashboard
refuses every page without sign-in:

- `SIF_INGEST_TOKEN`, `SIF_INTERNAL_TOKEN` and `SIF_DASHBOARD_TOKEN` set, random, at least 24 characters, and the dashboard token different from the others
- `SIF_SECRET_KEY` set, and no development database password
- a GitHub App instead of `GITHUB_TOKEN`, protected branches required, dependency changes off
- sandboxes under gVisor (`SIF_PODMAN_RUNTIME=runsc`) or on Kubernetes (`SIF_SANDBOX_BACKEND=kubernetes`)
- a real agent (`SIF_AGENT` not `scripted`)
- `SIF_LESSON_AUTO_APPROVE=false`: a person approves every lesson before it becomes an agent instruction
- `SIF_RETENTION_DAYS` and `SIF_AUDIT_RETENTION_DAYS` set to a number (0 keeps everything, as a decision rather than a default)

git-broker also refuses to push in production when its credentials have admin rights on the repository: an admin can merge past the
required review. A GitHub App's installation token never has admin.

Also: `SIF_AUTH_MODE=proxy` with the `sso` profile and portal-web's port removed. The 3D view then runs as `3d-view-prod` (a static
build, reads only) at `/3d/` behind the same sign-in. Remove the dev server's port 5173, and set `SIF_3D_URL=/3d/`.

## Intake and limits

**Admin → Teams → Intake** stops the factory from taking new tickets, for every team (operators) or one team (its leads). Running tasks
carry on; stop them from their page. Waiting tickets keep their label. Per team, `SIF_MAX_CONCURRENT_PER_TEAM` and
`SIF_MAX_TASKS_PER_TEAM_PER_DAY` (25) cap what starts. If portal-api cannot say whether intake is paused, nothing starts. The 3D view shows
a banner with the reason while new tickets are held back.

## Where code and tickets go

Ticket text, repository context and code go only to `LLM_UPSTREAM_URL`. Point it at a model endpoint inside your network (a LiteLLM
gateway, Amazon Bedrock in your account or region, or a self-hosted model) and nothing leaves it. Sandboxes have no internet access at all;
embeddings run locally by default (`EMBEDDINGS_MODEL_PATH` for machines without internet).

Before anything is indexed, secrets and personal data (card numbers, SSNs, IBANs, email addresses; `SIF_REDACT_PII`) are removed. Confluence
pages with read restrictions are skipped, and so are pages under them: the connector reads as one account, so indexing them would let every task of
the team search them. The search only returns documents of the task's team or repository.

## Audit and retention

The audit log is append-only in the database: an update or delete fails, except the retention job's. Every audited request is also printed
as one JSON line (`{"audit": {...}}`) on portal-api's stdout, so the log shipper can forward a copy to the SIEM. `SIF_RETENTION_DAYS` deletes
event payloads of finished tasks (ticket text, agent output, code) after that many days, and `SIF_AUDIT_RETENTION_DAYS` deletes old audit rows.
Both default to keeping everything.

## Backups

`make backup` writes a `pg_dump` of the whole database to `backups/`; `make restore FILE=…` puts one back. Store `SIF_SECRET_KEY`
separately from the backups: the dumps contain the connection secrets encrypted with it.

## Rotating secrets

Rotate a token by changing it in `.env` and recreating the services that use it. Any secret setting can instead be a mounted file
(`SIF_INGEST_TOKEN_FILE=/run/secrets/ingest`, for Kubernetes Secrets, External Secrets or Vault Agent).

To rotate `SIF_SECRET_KEY`, set the new key there and the old one in `SIF_SECRET_KEYS_OLD`, then restart portal-api. It re-encrypts every saved
connection secret with the new key on start. Then remove the old key.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request: the Python tests (Postgres and Temporal), the 3D view's unit, build and
screenshot tests, the dashboard build, the Helm charts, and security scans: gitleaks over the whole history, Trivy for vulnerable
dependencies, secrets and misconfigurations (accepted findings, each with its reason, in `.trivyignore.yaml`), and Semgrep (the rules
excluded after review are listed in the workflow). Each run keeps a CycloneDX SBOM. On main and on tags the images are built, scanned for
critical vulnerabilities, pushed to `ghcr.io/<owner>/<repo>/<component>`, signed keylessly with cosign and given their SBOM as an
attestation. Verify one before deploying:

```
cosign verify ghcr.io/<owner>/<repo>/portal-api@sha256:... \
  --certificate-identity-regexp 'https://github.com/<owner>/<repo>/.github/workflows/ci.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

Dependabot (`.github/dependabot.yml`) opens weekly update PRs for actions, Python, npm and base images.
