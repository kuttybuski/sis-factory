# After the pull request opens

A task does not end at the draft PR. The factory follows it until a person merges or closes it, so reviewers only review.

| On the PR | What the factory does |
|---|---|
| A review that **requests changes** (and its inline comments) | One revision round: an agent works from the PR branch with the comments and pushes a commit onto it, then says on the PR what changed. |
| A PR comment or review that **mentions `@sis`** | The same. Comments without the mention are people talking to each other and are left alone. |
| **CI fails** on the head commit | The same, with the failing checks' output. Each head commit is handled once. |
| **Merged** | Task ends as *merged*; Jira gets `sis-merged`. Commits people added to the branch first are recorded as *fixups* for the improvement cycle (docs/self-improvement.md). |
| **Closed** without merging | Task ends as *closed*; Jira gets `sis-closed`. |
| Nobody decides in 14 days, or someone kills the task | Task ends as *PR left open*: the factory stops following it and a person finishes it. The dashboard and 3D view say why and who stopped it. |

Limits: `max_review_rounds` (3) revisions per PR, after which the factory asks the reviewer to finish by hand; each round may spend up to
one budget extension (`SIS_BUDGET_EXTENSION_USD`); the factory stops watching a PR nobody acts on after 14 days. PR comments are screened
by the injection guard like ticket text. Pushes are fast-forward commits on the `sis/*` branch, never force-pushes, and protected paths
(CI configuration, CODEOWNERS) still cannot be changed. A PR waiting for review does not take one of the concurrency slots
(`SIS_MAX_CONCURRENT_TASKS`): it runs as `ReviewWorkflow` under the same workflow id. `SIS_REVIEW_POLL_SECONDS=0` turns this off.
