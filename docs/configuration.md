# Configuration reference

`.env.example` lists only what a team must set. Everything below is optional: the values shown are the defaults the code uses, so set one
in `.env` only to change it.

## Agent and model

| Setting | Default | Meaning |
|---|---|---|
| `SIF_AGENT` | `scripted` | `claude` runs Claude Code in the sandbox (build the agent-runner image with `INSTALL_CLAUDE=true`); `scripted` is the deterministic demo agent that follows `edit … replace …` lines and calls no model. The dashboard shows a warning while it is `scripted`. |
| `SIF_PROFILE` | `cheap-best` | Name passed to the agent for model routing. |
| `LLM_UPSTREAM_AUTH` | `bearer` | `x-api-key` for api.anthropic.com. |
| `LLM_UPSTREAM_URL` | – | Any Anthropic-compatible endpoint, including one inside your network (a LiteLLM gateway, Amazon Bedrock, a self-hosted model). Code only leaves your network if this URL does. |

## Jira behaviour

| Setting | Default | Meaning |
|---|---|---|
| `SIF_LABEL_TRIGGER` / `SIF_LABEL_PICKED` / `SIF_LABEL_TIMED_OUT` | `sif` / `sif-picked` / `sif-timed-out` | Labels the factory reads and sets. |
| `SIF_MENTION` | `@sif` | Comments (Jira and PR) must contain this to reach the agent. |
| `SIF_BOT_ACCOUNT_ID` | – | Jira account id of the bot user, so real @mentions also match. |
| `SIF_JIRA_TYPE_MAP` | `epic:epic,story:story,task:task,bug:task,…` | Jira issue type → kind; unmapped types are tasks. |
| `SIF_MAX_CONCURRENT_TASKS` | `5` | Tasks building at once. PRs waiting for review do not count. |
| `SIF_MAX_RUN_MINUTES` | `240` | Longest single agent run. |
| `SIF_MAX_CONCURRENT_PER_TEAM` | `0` | Running tasks per team (`0` = only the overall limit). |
| `SIF_MAX_TASKS_PER_TEAM_PER_DAY` | `25` | Tasks a team can start in 24 hours (`0` = no limit). Extra tickets keep their label and wait. Both per-team limits are set on portal-api, which reports them on `GET /api/intake` to jira-connector, the dashboard and the 3D view. |

## Pull requests after they open

| Setting | Default | Meaning |
|---|---|---|
| `SIF_REVIEW_POLL_SECONDS` | `60` | How often an open PR is checked for review comments, failing CI and merge. `0` stops at the draft PR. |
| `SIF_PROTECTED_PATHS` | CI definitions (GitHub Actions workflows and actions, GitLab, Jenkins, CircleCI, Azure, Buildkite, Bitbucket, Travis), `CODEOWNERS`, `.sif/**` | Paths agents may never change (`gitops.DEFAULT_PROTECTED`). |
| `SIF_ALLOW_DEPENDENCY_CHANGES` | `false` | Agents may not change manifests or lockfiles (`SIF_DEPENDENCY_FILES`, `gitops.DEFAULT_DEPENDENCY_FILES`); a person adds or upgrades dependencies. Cannot be on in production. |
| `SIF_MAX_CHANGED_FILES` / `SIF_MAX_CHANGED_LINES` | `50` / `3000` | Larger patches are refused: split the ticket. |
| `SIF_REQUIRE_PROTECTED_BRANCH` | `true` in production, else `false` | Refuse to open a PR onto a branch that does not require an approving review. |
| `SIF_TRUSTED_ASSOCIATIONS` | `OWNER,MEMBER,COLLABORATOR` | Whose PR comments count as feedback for the agent and for learning (GitHub `author_association`). |
| `GITHUB_APP_ID` / `GITHUB_APP_PRIVATE_KEY` | – | Use a GitHub App instead of `GITHUB_TOKEN` (docs/security.md). |

## Handling (epics, features, stories, tasks)

Edited on the dashboard (Admin → Handling). `SIF_HANDLING_MAX_CHARS` (4000) limits instruction length; `SIF_HANDLING_MAX_REVISIONS` (3)
limits feedback rounds on intent.md, design.md or a split.

## Lessons (`@sif remember …`)

`SIF_CMD_REMEMBER` (`remember`), `SIF_CMD_FORGET` (`forget`), `SIF_LESSON_AUTO_APPROVE` (`true`), `SIF_LESSON_DEFAULT_TTL_DAYS` (180),
`SIF_LESSON_MAX_IN_PROMPT` (8), `SIF_LESSON_TOKEN_BUDGET` (800), `SIF_LESSON_GLOBAL_REQUIRES_EVAL` (`false`), `SIF_LESSON_HOLDOUT_PCT` (20: share of
tasks a machine-proposed lesson is withheld from, so its effect can be measured). Rules that belong to a repository
are better kept in that repository's `CLAUDE.md`, where they are reviewed like code; Claude Code reads it when it works in the repository
(`SIF_AGENT=claude`).

## Self-improvement (docs/self-improvement.md)

`SIF_IMPROVE_MINUTES` (60; `0` = only from the dashboard), `SIF_IMPROVE_DAYS` (30), `SIF_IMPROVE_MIN_TASKS` (10: finished tasks needed with and
without a lesson before it is judged).

Replay benchmark: `SIF_EVAL_MAX_CASES` (20), `SIF_EVAL_BUDGET_USD` (1.0 per replay; a run costs up to 2 × cases × this), `SIF_EVAL_PASS_SCORE`
(0.5), `SIF_EVAL_AUTO` (`false`: replay every lesson the cycle proposes).

Daily knowledge PR: `SIF_DIGEST_HOURS` (24; `0` = no PRs, candidates are approved on the Learning page), `SIF_DIGEST_FILE` (`CLAUDE.md`),
`SIF_DIGEST_POLL_SECONDS` (300, orchestrator-worker: how often it opens due PRs and reads back merged ones).

## Context index

`EMBEDDINGS_URL` (`http://embeddings:8080`), `EMBEDDINGS_MODEL` (`BAAI/bge-small-en-v1.5`), `EMBEDDINGS_API_KEY`, `EMBEDDINGS_MODEL_PATH`
(pre-downloaded model, no internet needed), `EMBEDDINGS_FALLBACK` (`hash`), `SIF_PR_HISTORY_COUNT` (100), `SIF_CONTEXT_SYNC_MINUTES` (60),
`SIF_REVIEW_MIN_COUNT` (2), `SIF_QA_REUSE_SIM` (0.8). Confluence reuses the Jira site and token unless `CONFLUENCE_*` are set.
`SIF_CONFLUENCE_RESTRICTED` (`skip`: pages with read restrictions, and pages under them, are not indexed; `index` keeps them).
`SIF_REDACT_PII` (`card,ssn,iban,email`; `none` turns it off): personal data removed before indexing.

## Sign-in and roles

See `components/auth-proxy/README.md`: `SIF_AUTH_MODE`, `SIF_PORTAL_ADMINS`, `SIF_PORTAL_OPERATORS`, `SSO_*`. Per-team leads are set on
the Teams page. `SIF_3D_URL` (`http://localhost:5173`; `/3d/` behind auth-proxy).

## Production and retention

| Setting | Default | Meaning |
|---|---|---|
| `SIF_ENV` | – | `production`: services refuse to start with unsafe settings (`sif_contracts/posture.py`), and the dashboard refuses to run without sign-in. |
| `SIF_DASHBOARD_TOKEN` | – | The dashboard's own token for people's decisions (`make init` creates it). Without it, local development falls back to `SIF_INGEST_TOKEN`. |
| `SIF_SECRET_KEYS_OLD` | – | Previous `SIF_SECRET_KEY` values while rotating. |
| `SIF_RETENTION_DAYS` | `0` | Delete event payloads (ticket text, agent output, code) of finished tasks after this many days. `0` keeps them. |
| `SIF_AUDIT_RETENTION_DAYS` | `0` | Delete audit rows after this many days. `0` keeps them. |
| `<SECRET>_FILE` | – | Any secret setting can be read from a mounted file instead (`sif_contracts/secrets.py`). |

## Connections

`SIF_ALLOW_PRIVATE_URLS` (`false`): set `true` when a team's Jira or GitHub is self-hosted on a private address.
