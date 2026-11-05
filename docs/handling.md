# Handling epics, features, stories and tasks

The factory decides what to do with a Jira ticket from its **kind**, using a **handling policy**. The factory sets one policy per kind on the dashboard
(**Handling** page). A team (a Temporal namespace) can override any field; fields a team leaves on *inherit* follow the factory.

## Kinds

The Jira issue type name is mapped to a kind by `SIS_JIRA_TYPE_MAP` (default: `Epic`, `Story`, `Task`, `Bug`, `Sub-task`, `Feature`, `New Feature`).
A type that is not in the map is treated as a `task`. Jira has no built-in Feature type; add one and map it, or leave it out.

## The policy

| Field | Meaning |
|---|---|
| `instructions` | Free text added to the trusted guidance of every agent that works on this kind. |
| `split_into` | `none`, `story` or `feature` (epics and features only). A planner agent proposes child issues. |
| `split_approval` | A person approves the proposed split on Jira before any child issue is created. |
| `intent` / `intent_approval` | Write `intent.md` (problem, outcome, scope, open questions); optionally a person approves it. |
| `design` / `design_approval` | An architect agent writes `design.md` from the story and the intent; optionally a person approves it. |
| `implement` | The developer agent follows the documents and opens the draft pull request. |

Rules (checked when you save): only epics and features can be split; a split kind hands the work to its children, so it has no intent, design or
implementation of its own; an approval needs its stage; a kind that is not split must be implemented.

Built-in defaults, used until someone saves a policy:

- **story, task**: no intent, no design, developer opens the draft PR. This is how the factory worked before policies existed.
- **epic, feature**: planner proposes stories, a person approves the split, the stories are created.

### Inheritance

`team override  >  factory setting  >  built-in default`, field by field. The Handling page shows where each value comes from. If a factory change
would leave a team override that no longer makes sense (for example it turns off `design` while a team still requires `design_approval`), the save is
refused and names the team.

A running ticket keeps the policy it started with: jira-connector copies the resolved policy into the workflow when it picks the ticket up.

## What happens to a ticket

```
epic / feature   planner -> [person approves the split] -> child issues created (label sis, parent link) -> done ("split")
                                                            each child is picked up on the next poll and handled by its own kind's policy

story / task     [intent agent -> intent.md -> person approves] -> [architect agent -> design.md -> person approves] -> developer agent -> draft PR
```

Every agent runs in its own sandbox, one after the other; the sandbox is stopped while a person reads a document. The budget is for the whole ticket:
each agent gets what is left of it, and the cost shown is the sum.

### Approving on Jira

The documents are posted as a comment. Reply to it:

- `@sis approve` accepts the document and moves on.
- `@sis stop` ends the ticket.
- `@sis <what to change>` sends it back to the same agent with your feedback and its previous draft. After `SIS_HANDLING_MAX_REVISIONS` rounds (default 3) only
  approve or stop are accepted.

Waiting is bounded by `SIS_WAIT_TIMEOUT_MINUTES` like any other question: if nobody replies, the ticket gets `sis-timed-out` and restarts when the label is added again.
A split that is approved but cannot be completed (Jira refuses a child) fails the ticket and lists the children that were already created.

### Where the documents end up

`intent.md` and `design.md` are written into the pull request under `docs/sis/<TICKET>/`, so they are reviewed and versioned with the code. A document that
nobody approved is marked *"Written by an agent. No person has reviewed this document."* at the top.

## Safety

The instructions are part of what the agents are told to obey, so they are treated like lessons: limited to `SIS_HANDLING_MAX_CHARS` characters and refused if
injection-guard blocks them or they look like they contain a secret. Changing them needs the admin role and is recorded in the audit
log (docs/security.md). Documents the agents write go through the gateway's outbound screening before they reach the workflow.

## API

| | |
|---|---|
| `GET /api/handling` | built-in defaults, factory rows and every team override |
| `GET /api/handling/effective?kind=&team=` | the resolved policy and where each field came from |
| `PUT /api/handling/factory/{kind}` | body `{"fields": {...}}` |
| `PUT /api/handling/teams/{team}/{kind}` | body `{"fields": {...}}`; only the fields to override |
| `DELETE /api/handling/teams/{team}/{kind}` | back to inheriting everything |
