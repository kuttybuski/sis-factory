# Team knowledge

Self-learning finds the subtle conventions from what reviewers correct (docs/self-improvement.md). It cannot find the basics a new senior
developer is told on day one: what the domain words mean, which systems the team owns and who owns the neighbours, the rules that must
never be broken, what "done" means. Those are known up front and too important to learn by getting them wrong, so a person writes them
down on **Admin → Team knowledge**, one page per team. Both reach the agent: the team's page first, then the learned rules.

## Sections

| Section | Sent to the agent | Limit |
|---|---|---|
| Non-negotiables | with every ticket | 3000 characters |
| Definition of done | with every ticket | 2000 |
| Glossary (`Term: meaning`, one per line) | an entry, when the ticket mentions the term | 8000 |
| Systems and ownership (`system: what it does, owner`, one per line) | an entry, when the ticket mentions the system | 8000 |
| How we work | while it fits | 3000 |
| Other things a new senior would need to know | while it fits | 3000 |

"Mentions" is a whole-word, case-insensitive match against the ticket's title and description; `billing-api` also matches "billing api".
The block is at most 6000 characters, in the order above, and tells the agent to stop and ask when the ticket conflicts with a
non-negotiable. **What an agent would get** at the bottom of the page shows the exact block for a pasted ticket.

## Safety

The text becomes part of the agent's instructions, so it is treated like a lesson:

- Editing needs the team's leads when it has some (Teams page), otherwise the operator role; admins always may.
- Every save is scanned. Text that looks like an injection attempt, or like a secret, is refused. A suspicious phrase is saved but flagged on the page.
- Saves are versioned. Each save says which version it started from, and if someone saved in between, the save is refused, without losing
  what you typed. Earlier versions are listed under each section.
- A section not confirmed for 90 days is marked **check it is still true**. **Still true** confirms it without changing it.
- Deleting a team removes its page, but keeps the history.

## Suggestions

The page suggests additions. Nothing is added until someone clicks **Add**, and **Dismiss** hides a suggestion for good:

- **found in your docs**: `Term: meaning` lines in the team's indexed Confluence pages and docs, and the first sentence of each repository's README.
- **learned by the factory**: approved team-scoped lessons, and the same repository lesson learned in two or more of the team's repositories.
  These are the team-wide rules self-learning found.
- **reviewers keep raising this**: a written non-negotiable that reviewers still ask for on two or more tasks in 90 days. This needs a
  person: reword the rule, or enforce it with a check. There is no Add button.
- **not described yet**: a repository with no README to draft from. Write its line yourself.

## API

| | |
|---|---|
| `GET /api/teams/{team}/knowledge` | every section, with its version, flags, who saved and confirmed it, and whether it is stale |
| `PUT /api/teams/{team}/knowledge/{section}` | `{"content", "version", "by"}`: 422 if refused, 409 if someone saved first |
| `POST /api/teams/{team}/knowledge/{section}/confirm` | still true |
| `GET /api/teams/{team}/knowledge/{section}/history` | earlier versions |
| `GET /api/teams/{team}/knowledge-preview?q=` | the block an agent would get for a ticket with this text |
| `GET /api/teams/{team}/knowledge-suggestions`, `POST …/knowledge-suggestions/dismiss` | suggestions |

Writes need the dashboard token, which only the dashboard server holds. `GET /api/lessons/relevant` returns the block as `knowledge`, and
context-api puts it in front of the lesson rules in the agent's context pack.
