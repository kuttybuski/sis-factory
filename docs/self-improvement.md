# How the factory improves itself

Every merged, revised or rejected PR is feedback on how well the agent worked. The factory collects that feedback, turns what recurs into
rules, checks whether each rule actually helps, and drops rules that make things worse. It does this without fine-tuning a model, and
without anyone writing a prompt.

```
 signal                        →  proposal              →  judged before use         →  adopted                       →  measured in use
 reviewers send a PR back         candidate lesson         replay on past tickets       one knowledge PR per repo        tasks with vs. without
 CI fails on the factory's PR     (repo scope)             (with vs. without it)        per day, into CLAUDE.md;         (random holdout);
 people commit before merging                                                           merging is the approval          retired if it hurts
 tickets need a question          proposal for people  ──────────────────────────────────────────────────────────────→  a person acts
 a rule keeps being re-asked
```

## Signals

All signals come from the factory's own event log, so they exist for every team without extra setup:

| Signal | Event | What it says |
|---|---|---|
| Review feedback | `review.received` (each item: reviewer, file, text) | what the first attempt got wrong |
| CI failures | `review.received` (CI items, by check name) | what the agent should have run before finishing |
| Human fixups | `pr.merged` `fixups` + commit messages (git-broker reads the PR's commits made by people) | what a person still had to fix by hand |
| Intake gaps | clarifying questions and bounces | what tickets keep leaving out |
| Outcomes | merged / closed, review rounds, fixups per task | whether any of the above improved |

context-ingest separately mines review threads on *all* merged PRs in a team's repositories, including PRs written by people
(`SIS_REVIEW_MIN_COUNT`), so the factory learns team conventions before it has a history of its own.

## Proposals

The cycle groups similar review remarks across different tasks (the same remark on at least two PRs is a convention, not a one-off) and
recurring CI failures per check, and adds them as **candidate** lessons scoped to the repository (`source: mined`). Remarks that are close
to an existing lesson are skipped, including rejected and disabled ones, so a rule people turned down is not proposed again. Candidates go
through the same screening as every lesson (injection and secret scans) and never reach the agent until a person approves them.

## Judging a lesson before it is used: the replay benchmark

Every factory PR that merges becomes a **replay case**: the ticket, the commit the factory started from, and the diff from there to what was
merged (`eval_cases`). Because that diff includes review revisions and human fixups, it is what the team finally accepted.

**Replay** on a lesson (Learning page, operator role, or `POST /api/lessons/{id}/replay`) re-runs the most recent cases in the lesson's
scope (up to `SIS_EVAL_MAX_CASES`, 20) twice in sandboxes: once with the lesson forced into the rules, even if it is still a candidate,
and once without it. orchestrator-worker picks the request up and runs an `EvalWorkflow`. Each case's two arms run side by side as
`ReplayWorkflow`s, the same developer agent with the same sandbox and budget cap (`SIS_EVAL_BUDGET_USD` per replay) but no Jira and no PR,
and questions get a standing "no one is available, decide and say so" answer.

Each patch is scored against the reference: 40% overlap of touched files, 60% overlap of changed lines. A score of `SIS_EVAL_PASS_SCORE`
(0.5) or more counts as solving the ticket. The two counts go through the same gate as every eval (`learn.passes_gate`: at least 10
tickets, and no worse than without), and are written onto the lesson, so the knowledge PR shows them.

Similarity to the merged diff is a proxy: a different correct fix scores lower than it deserves. The proxy is biased the same way on both
arms of a case, so the comparison is still fair. A replay run costs up to 2 × cases × `SIS_EVAL_BUDGET_USD`. That is why
`SIS_EVAL_AUTO` (replay every lesson the cycle proposes) is off by default.

## Adopting lessons: the daily knowledge PR

Candidate lessons for a repository are not reviewed one by one. At most once every `SIS_DIGEST_HOURS` (24) per repository, its new
candidates go into **one pull request** that appends them to a managed block in the repository's `SIS_DIGEST_FILE` (`CLAUDE.md`). The PR
says, for each lesson, where it came from, its evidence and its replay result.

- **Merge** it: the lessons still in the block are adopted (approved, by whoever merged), and `in_repo` from then on. The factory stops
  sending them in the rules block, because Claude Code reads `CLAUDE.md` itself, and the rule is now versioned and reviewed like code.
- **Delete a line** before merging: that lesson is rejected.
- **Close** the PR: the whole batch is rejected, and none of it is proposed again.
- Lines already in the block are never rewritten, so edits people make there are kept. A lesson whose line someone later removes from
  `CLAUDE.md` is disabled at the next knowledge PR's merge.

While a knowledge PR is open, new candidates for that repository wait for the next one. Team and global lessons (no single repository)
are still approved on the Learning page. `SIS_DIGEST_HOURS=0` turns the PRs off, and every candidate is approved there.

Adopted lessons are no longer held out (they are in the repository for every task). They were judged by replay before the PR, and the
cycle still reports when reviewers keep asking for what one of them says.

## Measurement: the holdout

The question is whether a lesson changed outcomes. Comparing tasks before and after it was approved does not answer that, because the
repository, the tickets and the reviewers change in the meantime. So every lesson that no person wrote (mined, from PR reviews, suggested by
the agent) is withheld from a random `SIS_LESSON_HOLDOUT_PCT` (20%) of the tasks it would apply to. Which tasks are held out is decided by a
hash of task and lesson. A task's first attempt and every review round therefore land in the same group, and each lesson's groups are
independent of the others'. Both groups are recorded (`lesson_uses`).

Each finished task gets a quality score: 1 if it merged as delivered, 0.5 if it merged after revision rounds or human commits, 0 if it was
not merged. A lesson is judged once both groups have `SIS_IMPROVE_MIN_TASKS` (10) finished tasks. A difference counts only if it is at
least 0.1 and larger than 1.64 standard errors.

- **hurts**: the cycle disables the lesson and records the numbers as the reason (`retired automatically: …`).
- **helps**: the result is recorded as a passed eval (`eval_status`). That passed eval is what `SIS_LESSON_GLOBAL_REQUIRES_EVAL` asks for.
- **no effect**, after three times the minimum: proposed for removal, because every rule costs prompt budget and attention.

Lessons a person taught are always delivered and never retired automatically. The cycle still reports when reviewers keep asking for what
such a lesson says on tasks that were given it. That means the rule is worded so it cannot be followed, or it belongs in a linter or CI.

## Proposals for people

What the cycle cannot change on its own appears on **Learning → How the factory is improving**:

- a detail that tickets keep leaving out → add it to the Jira issue template or the team's handling instructions
- a lesson that is not being followed → reword it, or enforce it with a check
- a lesson with no effect → disable it
- a repository where most PRs need rework (first-time-right below 50%) → write what reviewers ask for into its `CLAUDE.md`
- candidate lessons waiting for review

Two more go to the team knowledge page as suggestions (docs/team-knowledge.md): a lesson learned in several of a team's repositories,
offered as a team non-negotiable, and a written non-negotiable that reviewers keep raising anyway.

The page also shows first-time-right per repository: of the PRs people decided on, the share merged with no revision round and no human
commits. That is the factory's main quality number.

## What it may change by itself

It adds candidate lessons and opens knowledge PRs, but a person merges every one. It disables an approved lesson that no person wrote once
the holdout shows that the lesson makes outcomes worse. It does not approve lessons on its own, merge anything, change handling, budgets
or prompts, or open tickets. Every run is stored (`improvement_runs`) with what it found and what it changed. Its lesson changes appear on the lesson itself
(approver, reason, eval).

## Running it

portal-api runs the cycle every `SIS_IMPROVE_MINUTES` (60; `0` = only on demand), looking back `SIS_IMPROVE_DAYS` (30). Measurements use
three times that window. Operators can also start a run from the Learning page.

| | |
|---|---|
| `POST /api/improve/run` | run the cycle now (ingest token) |
| `GET /api/improve?team=` | latest report, earlier runs, live lesson measurements, first-time-right per repository |
| `GET /api/lessons/relevant?task_id=…&record_use=true` | what context-api calls: returns the lessons for the task and records the treatment and comparison groups (`include` / `exclude` force one lesson in or out, for replays) |
| `POST /api/lessons/{id}/replay` | replay recent merged tickets with and without the lesson |
| `GET /api/eval/cases`, `GET /api/eval/runs` | replay cases per repository; replay runs and their results |
| `GET /api/digests` | knowledge PRs and their status |

## Not built yet

These use the same signals, the replay benchmark and the holdout gate. Each is a separate change:

- **Scoring by tests.** Run the repository's tests on each replay patch (needs env-builder's per-repository images) and score by
  pass/fail instead of, or as well as, similarity.

- **Handling and prompt variants.** Run two versions of a team's handling instructions side by side on random halves of its tickets,
  measured the same way as lessons.
- **Model routing from outcomes.** Learn the cheapest profile that still merges, per repository and kind of ticket, from cost and
  quality per profile (`/api/calibration?group=profile` already has the numbers).
- **Agent retrospective.** After a PR merges with rework, ask the agent to explain what it should have done differently, and propose that
  as an `agent_suggestion` lesson.
- **Sandbox fixes from recurring environment errors.** Turn missing tools and failing installs in the retro into env-builder changes.
