# Team connections (Jira and GitHub)

Each team can connect its own Jira site and GitHub account on the dashboard (**Connections** page). A team without a connection uses the
shared credentials from the environment (`JIRA_*`, `GITHUB_TOKEN`), as before.

## What the page shows

| Status | Meaning |
|---|---|
| Connected | The last check signed in and could see every Jira project / read every repository listed for the team. |
| Failed | The check failed; the reason is shown (bad token, a project the account cannot see, unreachable URL...). |
| Not checked yet | Saved, but the check has not run. |
| Using shared default | No connection saved for this team. |

**Save & test** stores the connection and checks it immediately; **Test again** re-checks; **Remove** returns the team to the shared credentials.

What you enter:

- **Jira**: site URL (`https://your-site.atlassian.net`), the bot account's email, and its API token.
- **GitHub**: a token that can read the team's repositories, push `sif/*` branches and open pull requests. For GitHub Enterprise, set the API and Git URLs.

## Who uses the credentials

| Service | Uses |
|---|---|
| jira-connector | Each team's Jira account for that team's projects (search, comments, labels, created stories). The shared account searches every other project. |
| git-broker | The team's GitHub token to fetch, push and open the pull request. |
| context-ingest | The team's Jira site for Confluence, and its GitHub token for PR history and docs. |

A changed token takes effect within `SIF_CONNECTION_CACHE_SECONDS` (default 60). If portal-api cannot be asked whether a team has a connection, the services
skip that work rather than use the shared account: acting as the wrong identity is worse than waiting.

## Security

- Tokens are encrypted with `SIF_SECRET_KEY` (`make init` generates it) and are never returned by the API; only the last four characters are shown. If the key is
  lost or changed, stored tokens cannot be read and must be entered again.
- Only callers holding `SIF_INTERNAL_TOKEN` can read a decrypted token (`GET /api/internal/connections/{team}/{provider}`). Saving, testing and removing need the ingest
  token, which the dashboard server holds.
- Changing a URL or email requires entering the token again, so a stored token is never sent to a new address.
- URLs must use https and must not point at private, loopback or link-local addresses. Set `SIF_ALLOW_PRIVATE_URLS=true` for a self-hosted Jira on a private network.
- **Only admins can change connections**, and only with sign-in turned on (`SIF_AUTH_MODE=proxy`, docs/security.md). Without it anyone who can open the
  dashboard can change a team's credentials: do not enter real tokens that way.

## API

| | |
|---|---|
| `GET /api/connections` | status of every saved connection (no secrets) |
| `PUT /api/connections/{team}/{jira\|github}` | `{"config": {...}, "secret": "..."}`; blank secret keeps the stored one; checks right away |
| `POST /api/connections/{team}/{provider}/test` | check again |
| `DELETE /api/connections/{team}/{provider}` | back to the shared credentials |
