# Teams

A team is a Temporal namespace named after it: "Mobile Apps" runs in `mobile-apps`. Each team's tickets run as workflows in that namespace,
so they are listed, retained and searched apart from other teams' in Temporal. Tickets in a Jira project that belongs to no team run in `default`.

## Creating a team

On the dashboard, **Teams** -> **Create team**: a name, the team's Jira project keys, its repositories (`owner/name`) and, optionally,
Confluence spaces. Saving registers the namespace in Temporal (retention `SIF_NAMESPACE_RETENTION_DAYS`, default 30). The workers look for
new namespaces every `SIF_NAMESPACE_REFRESH_SECONDS` (default 15) and start serving them, with no restart.

Rules checked when you save:

- The name cannot be changed later (it is the namespace), and two names cannot map to the same namespace ("Data Eng" and "data-eng").
- A Jira project belongs to one team only.
- A team with running tasks cannot be deleted. Deleting a team removes its handling overrides and connections; its task history and its
  Temporal namespace stay.

A team can name its leads (emails or sign-in groups). When it does, only they and admins edit its knowledge, review its lessons, stop its
tasks and pause its intake; the global operators no longer can (docs/security.md).

Each team also has a knowledge page (**Admin → Team knowledge**): its rules, definition of done, glossary and systems, written by the
team and sent to its agents (docs/team-knowledge.md).

## Choosing a team on the dashboard

The **Team** picker in the dashboard's top bar, next to **3D view**, is the outermost filter. Pick a team and every page shows only that
team: Overview, Cost, History, Workflows (pinned to its namespace), Handling (its overrides; *All teams* edits the factory defaults),
Connections, Teams, Learning (its retro, budgets and lessons, plus global, repo and path lessons) and Context (its sources and searches).
The 3D view link opens with `?team=<name>`, so the live floor shows only that team's tasks. The choice is kept in a cookie (`sif_team`)
for a year and is per browser; it is a view filter, not access control.

## Watching workflows

**Workflows** on the dashboard shows the Temporal UI, one team at a time. The dashboard proxies it under `/temporal`
(compose runs Temporal with `--ui-public-path /temporal`), which is also how it can be shown inside the page: Temporal only allows
same-origin framing.

It is read-only (`proxy.ts`): terminate, reset, signal and start are hidden and refused, because they would skip what **Kill task** on a
task's dashboard page does (stop the sandbox, record who asked). The 3D view's Kill button opens that page. For anything else, use the
`temporal` CLI inside the Temporal container.

A task waiting for a reply can also be answered in the 3D view's panel. The answer goes through the dashboard (`/answer`: the same people
who may kill the task, recorded in the audit log), and jira-connector posts it on the ticket as a comment *before* giving it to the
task, so Jira keeps the whole conversation even if Temporal's history is lost. With `SIF_DRY_RUN=true` the comment is only logged.

## API

| | |
|---|---|
| `GET /api/teams` | every team, with its `temporal_namespace` |
| `PUT /api/teams` | `{"name", "jira_projects", "repos", "confluence_spaces", "leads"}`; creates or updates, registers the namespace |
| `GET /api/teams/{name}/posture` | per repository: the factory's GitHub identity and whether the default branch requires a review |
| `GET /api/intake`, `PUT /api/intake/pause`, `DELETE /api/intake/pause/{team}` | intake pauses (`*` = every team) and per-team counts |
| `DELETE /api/teams/{name}` | refused while the team has running tasks |

Writes need the dashboard token (`SIF_DASHBOARD_TOKEN`, held by the dashboard server only). Creating, changing and deleting teams needs the admin role once sign-in is on (docs/security.md).
