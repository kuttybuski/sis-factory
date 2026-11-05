# Running a pilot

The question a pilot answers: of the tickets engineers hand to the factory, how many reach main with people only reviewing, how many review
rounds that takes, and what each merged change costs. The Overview page's **Scorecard by repository** shows exactly that; nothing in this
repository claims a number until a pilot produces one.

1. **Pick one repository and one team** with a working CI and tickets that are usually one to three days of work.
2. **Run the real agent**: `SIF_AGENT=claude`, build agent-runner with `INSTALL_CLAUDE=true`, and set `LLM_UPSTREAM_URL` / `LLM_UPSTREAM_KEY`
   (a model endpoint inside your network works the same way). The dashboard warns on every page while the demo agent is in use.
3. **Give it 50–100 tickets** the team would have done anyway, written as usual. Do not pre-clean them: thin tickets get a question, and
   how often that happens is part of the result.
4. **Review as normal.** Request changes on the PR or mention `@sif` in a PR comment; failing CI is picked up by itself. Merge or close.
5. **Read the scorecard after 30 days.** Merged ÷ (merged + closed) is the merge rate; *rounds / merge* is how often reviewers had to send
   work back; *not delivered* are tickets that never produced a PR (bounced, failed, timed out, stopped). Compare *per merged PR* with
   what the same work costs in engineering time.

Each task's page (Tasks → a ticket) is the evidence behind every number: the full trail from pickup to merge, who replied or approved,
the context the agent was given, every log line and the cost at each step.
