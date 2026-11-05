"""TaskWorkflow: one Jira ticket from pickup to its outcome.

What happens between pickup and the outcome follows the handling policy in the input (see sif_contracts.handling): a plain story or task goes
straight to a developer agent and a draft pull request; an epic or feature is split into child issues; a story can first get an intent.md and an
architect's design.md, each optionally approved by a person on Jira. Every agent runs in its own sandbox, one after the other.

Deterministic by construction: it only waits, signals and calls activities. Anything that touches a network lives in activities.py
(this worker) or in jira-connector (the only holder of Jira credentials)."""

import asyncio
import contextlib
import dataclasses
import re
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from sif_contracts import handling as H
    from sif_contracts.policy import Decision, classify_reply
    from sif_contracts import replay
    from sif_contracts.workflow import (EVAL_WORKFLOW, JIRA_QUEUE, ORCHESTRATOR_QUEUE, Q_ANSWER, Q_LOGS, Q_STATE, REPLAY_WORKFLOW, REVIEW_WORKFLOW, SIG_COST, SIG_DONE,
                                        SIG_FAILED, SIG_KILL, SIG_LOG, SIG_QUESTION, SIG_REPLY, AgentResult, Answer, EvalCase, EvalInput, Feedback, Question,
                                        ReplayInput, Reply, ReviewInput, TaskInput)
    from orchestrator_worker.classify import slug

NET = RetryPolicy(maximum_attempts=4, initial_interval=timedelta(seconds=1), backoff_coefficient=2.0)
ONCE = RetryPolicy(maximum_attempts=1)
SHORT, LONG = timedelta(seconds=60), timedelta(minutes=10)
UNREVIEWED = "> Written by an agent. No person has reviewed this document.\n\n"
JIRA_COMMENT_CHARS = 6000  # Jira rejects very long comments; the full text still goes to the next stage
APPROVAL_EVENT_CHARS = 3000  # how much of a document awaiting approval the dashboard and 3D view get
LOG_KEEP = 2000  # lines of the task's log the logs query returns
INCIDENTS = "injection-incidents"  # workflow.patched: replies are screened and attempts reported; runs started before this replay as they were


@workflow.defn(name="TaskWorkflow")
class TaskWorkflow:
    def __init__(self) -> None:
        self.stage = "starting"
        self.questions: dict[str, Question] = {}
        self.answers: dict[str, Answer] = {}
        self.asked: list[str] = []
        self.seen_comments: set[str] = set()
        self.replies: list[Reply] = []
        self.done: AgentResult | None = None
        self.failed: str | None = None
        self.killed_by: str | None = None
        self.cost = 0.0  # spend of the sandbox that is running now
        self.cost_base = 0.0  # spend of the stages that have finished
        self.reported_cost = 0.0
        self.docs: dict[str, str] = {}  # intent.md / design.md written so far
        self.children: list[str] = []
        self.log_buffer: list[str] = []
        self.log: list[str] = []  # everything the task logged, sandbox included, for the logs query (Temporal UI); the last LOG_KEEP lines
        self.budget = 0.0

    # ------------------------------------------------------------------ signals and queries
    @workflow.signal(name=SIG_QUESTION)
    def on_question(self, q: Question) -> None:
        if q.id not in self.questions:
            self.questions[q.id] = q
            self.asked.append(q.id)

    @workflow.signal(name=SIG_REPLY)
    def on_reply(self, r: Reply) -> None:
        if r.comment_id and r.comment_id in self.seen_comments:
            return  # the connector may deliver the same Jira comment twice (restart, retry)
        self.seen_comments.add(r.comment_id)
        self.replies.append(r)

    @workflow.signal(name=SIG_DONE)
    def on_done(self, r: AgentResult) -> None:
        self.done = self.done or r

    @workflow.signal(name=SIG_FAILED)
    def on_failed(self, message: str) -> None:
        self.failed = self.failed or message

    @workflow.signal(name=SIG_KILL)
    def on_kill(self, by: str) -> None:
        self.killed_by = self.killed_by or by or "someone"

    @workflow.signal(name=SIG_COST)
    def on_cost(self, usd: float) -> None:
        self.cost = max(self.cost, usd)

    @workflow.signal(name=SIG_LOG)
    def on_log(self, lines: list[str]) -> None:
        self.log_buffer.extend(lines)
        self.keep("claude", lines)

    @workflow.query(name=Q_ANSWER)
    def get_answer(self, qid: str) -> Answer:
        return self.answers.get(qid, Answer(status="pending"))

    @workflow.query(name=Q_STATE)
    def state(self) -> str:
        return self.stage

    @workflow.query(name=Q_LOGS)
    def logs(self) -> list[str]:
        """What the 3D view's Logs tab shows, in one place in Temporal: the agent's lines, the factory's notes and outcomes, and the
        sandbox's own output when it stopped. Each line: time (UTC), source, text."""
        return self.log

    def keep(self, source: str, lines: list[str]) -> None:
        at = workflow.now().strftime("%H:%M:%S")
        self.log = [*self.log, *(f"{at} {source:<8} {line}" for line in lines)][-LOG_KEEP:]

    # ------------------------------------------------------------------ helpers
    def ts(self) -> int:
        return int(workflow.now().timestamp() * 1000)

    def spent(self) -> float:
        return self.cost_base + self.cost

    async def emit(self, inp: TaskInput, type: str, **fields) -> None:
        """Events feed the dashboard and the 3D view. Losing one must never fail a task."""
        if type == "log.line":
            if fields.get("source") != "claude":  # the agent's own lines were kept when they arrived (on_log)
                self.keep(fields.get("source", "temporal"), [str(fields.get("text", ""))])
        elif fields.get("text"):
            self.keep(type, [str(fields["text"])])
        try:
            await workflow.execute_activity("emit_event", args=[{"type": type, "taskId": inp.jira_key, "ts": self.ts(), **fields}],
                                            start_to_close_timeout=SHORT, retry_policy=RetryPolicy(maximum_attempts=3))
        except ActivityError:
            workflow.logger.warning("could not emit %s", type)

    async def jira(self, inp: TaskInput, text: str, add: list[str] | None = None, remove: list[str] | None = None) -> None:
        """A Jira comment notifies people, so it is kept for what someone must act on and for outcomes; progress goes to note(). An empty
        text changes only the labels."""
        for name, args in (("jira_comment", [inp.jira_key, text]), ("jira_set_labels", [inp.jira_key, add, remove])):
            if (name == "jira_set_labels" and not (add or remove)) or (name == "jira_comment" and not text):
                continue
            try:
                await workflow.execute_activity(name, args=args, task_queue=JIRA_QUEUE, start_to_close_timeout=SHORT, retry_policy=NET)
            except ActivityError:
                workflow.logger.warning("jira call %s failed", name)

    async def note(self, inp: TaskInput, text: str) -> None:
        """Progress worth keeping but not worth a Jira notification: it goes on the task's log only (dashboard, 3D view, Temporal history)."""
        await self.emit(inp, "log.line", source="temporal", text=text)

    async def act(self, name: str, *args, timeout: timedelta = SHORT, retry: RetryPolicy = NET):
        return await workflow.execute_activity(name, args=list(args), task_queue=ORCHESTRATOR_QUEUE, start_to_close_timeout=timeout, retry_policy=retry)

    async def screen(self, inp: TaskInput, text: str, source: str, by: str = "") -> dict:
        """Scans untrusted text before an agent sees it, with the team's blocklist. Anything not plainly allowed is reported as an incident:
        portal-api keeps it, and the team's repository gets a security PR recording it (orchestrator_worker.security)."""
        scan = await self.act("scan_text", text, source, inp.team)
        if scan["verdict"] != "allow" and workflow.patched(INCIDENTS):
            incident = {"team": inp.team or "", "repo": inp.repo, "jira_key": inp.jira_key, "source": source, "verdict": scan["verdict"], "score": scan["score"],
                        "rules": scan["rules"], "fingerprint": scan["fingerprint"], "actor": by}
            try:
                await self.act("report_injection", incident)
            except ActivityError:
                workflow.logger.warning("could not record the injection attempt on %s", inp.jira_key)
        return scan

    async def refused(self, inp: TaskInput, reply: Reply) -> bool:
        """A reply that looks like instructions for the agent is not passed on: the person is told, and the wait goes on for a plain answer."""
        scan = await self.screen(inp, reply.text, "jira_comment", reply.author)
        if scan["verdict"] != "block":
            return False
        who = reply.author or "someone"
        await self.emit(inp, "log.line", source="temporal", text=f"A reply from {who} was not passed to the agent: it looks like an attempt to instruct it.")
        await self.jira(inp, f"{inp.mention} did not pass the reply from {who} to the agent: it looks like an attempt to instruct it ({', '.join(scan['rules'][:3])}). "
                             "Please reply again with a plain answer.")
        return True

    async def fail(self, inp: TaskInput, why: str) -> str:
        """Ends the task as failed, saying why the same way on Jira (after the mention) and on the dashboard and 3D view."""
        await self.emit(inp, "task.failed", text=(why[:1].upper() + why[1:])[:1000])
        await self.jira(inp, f"{inp.mention} {why}", remove=["sif-picked"])
        return "failed"

    async def stopped(self, inp: TaskInput, by: str = "", labels: tuple[str, ...] = ("sif-picked",)) -> str:
        """Ends the task because a person stopped it: a kill from the dashboard (killed_by) or a `stop` reply on Jira (by)."""
        await self.emit(inp, "task.killed", actor=self.killed_by or by or None)
        why = f"stopped: the run was cancelled by {self.killed_by}." if self.killed_by else "stopped as requested."
        await self.jira(inp, f"{inp.mention} {why}", remove=list(labels))
        return "killed"

    async def context(self, inp: TaskInput) -> str:
        """The context pack for the next agent (documents, earlier answers, merged PRs, lessons, team knowledge), with what it holds on the log."""
        await self.emit(inp, "context.started")
        md = await self.act("build_context", inp.jira_key, inp.title, inp.description, inp.team, inp.repo, retry=ONCE)
        sections = [line[3:].strip() for line in md.splitlines() if line.startswith("## ")]
        sources = len(re.findall(r"^\[C\d+\] ", md, re.M))
        await self.emit(inp, "log.line", source="temporal",
                        text=f"Context ready: {sources} source(s) in {', '.join(sections) or 'no sections'} ({len(md)} characters)")
        return md

    async def flush(self, inp: TaskInput) -> None:
        """Forwards agent log lines and spend changes to the event stream."""
        lines, self.log_buffer = self.log_buffer, []
        for line in lines[:200]:
            await self.emit(inp, "log.line", source="claude", text=line)
        if abs(self.spent() - self.reported_cost) >= 0.005:
            self.reported_cost = self.spent()
            await self.emit(inp, "cost.updated", costUsd=round(self.spent(), 4))

    def workable(self) -> bool:
        return bool(self.done or self.failed or self.killed_by or self.log_buffer or abs(self.spent() - self.reported_cost) >= 0.005
                    or any(self.answers.get(q) is None for q in self.asked))

    # ------------------------------------------------------------------ main
    @workflow.run
    async def run(self, inp: TaskInput) -> str:
        self.budget = inp.budget_usd
        mention = inp.mention
        policy = H.from_dict(inp.policy) if inp.policy else H.resolve(inp.kind)[0]
        await self.emit(inp, "task.discovered", jiraKey=inp.jira_key, title=inp.title, team=inp.team, namespace=inp.namespace, repo=inp.repo, budgetUsd=self.budget)

        # 1. Triage. The ticket is untrusted input: scan it, then classify it.
        self.stage = "classifying"
        await self.emit(inp, "classify.started")
        scan = await self.screen(inp, f"{inp.title}\n{inp.description}", "jira_ticket", inp.reporter)
        if scan["verdict"] == "block":
            await self.emit(inp, "classify.result", classifier="ticket-quality", result="blocked")
            await self.emit(inp, "jira.bounced", text="The ticket text looks like an attempt to give the agent new instructions.")
            await self.jira(inp, f"{mention} did not start: the ticket text looks like an attempt to instruct the agent ({', '.join(scan['rules'][:3])}). A person should review it.",
                            add=["sif-blocked"], remove=["sif-picked"])
            return "bounced"
        ok, missing = await self.act("classify_quality", inp.title, inp.description, inp.kind)
        await self.emit(inp, "classify.result", classifier="ticket-quality", result="ok" if ok else "insufficient")
        risk = await self.act("classify_risk", inp.title, inp.description)
        await self.emit(inp, "classify.result", classifier="risk-tier", result=risk)
        profile = await self.act("classify_route", risk)
        await self.emit(inp, "classify.result", classifier="routing", result=profile)
        if not ok:
            clarified = await self.clarify(inp, missing)
            if isinstance(clarified, str):
                return clarified
            inp = clarified

        # 2. Code and context.
        self.stage = "checkout"
        try:
            checkout = await self.act("checkout_repo", inp.repo, inp.base_branch, inp.team, timeout=LONG)
        except ActivityError as e:
            return await self.fail(inp, f"could not check out {inp.repo}: {e.cause}")
        await self.emit(inp, "repo.checked_out")
        context_md = await self.context(inp)

        # 3. The policy decides the agents: split, or [intent] -> [architect] -> developer.
        commit = checkout["commit"]
        if policy.split_into != "none":
            return await self.split(inp, policy, commit, context_md, profile, risk)
        for role, name, needs_approval in (("intent", "intent.md", policy.intent_approval), ("architect", "design.md", policy.design_approval)):
            if (role == "intent" and policy.intent) or (role == "architect" and policy.design):
                outcome = await self.document(inp, commit, context_md, profile, risk, role, name, needs_approval)
                if outcome:
                    return outcome
        if not policy.implement:
            return "done"
        result = await self.run_agent(inp, commit, context_md, profile, risk, "developer", inp.description, dict(self.docs))
        if isinstance(result, str):
            return result
        return await self.publish(inp, commit, result, profile, risk)

    async def clarify(self, inp: TaskInput, missing: list[str]) -> TaskInput | str:
        """A thin ticket gets a question instead of a bounce: the answer is added to the description and the work starts.
        Only an unanswered question (wait_timeout_seconds) sends the ticket back."""
        m = inp.mention
        question = "Before I start, please tell me " + " and ".join(missing) + "."
        waited_since = len(self.replies)
        await self.emit(inp, "agent.asked_question", text=question, reason="question", deadline=self.ts() + inp.wait_timeout_seconds * 1000)
        await self.jira(inp, f"{m} {question}\n\nReply `{m} <the details>` and I will start, or `{m} stop`.", add=["sif-needs-info"])
        self.stage = "waiting_for_human"
        try:
            await workflow.wait_condition(lambda: len(self.replies) > waited_since or bool(self.killed_by), timeout=timedelta(seconds=inp.wait_timeout_seconds))
        except TimeoutError:
            await self.emit(inp, "jira.bounced", text="Ticket needs more detail: " + "; ".join(missing))
            await self.jira(inp, f"{m} did not start: no reply arrived. Add " + " and ".join(missing) + " to the description, then add the label again.",
                            add=["sif-needs-info"], remove=["sif-picked"])
            return "bounced"
        reply = None if self.killed_by else self.replies[waited_since]
        if reply is None or classify_reply(reply.text) is Decision.STOP:
            return await self.stopped(inp, reply.author if reply else "", ("sif-picked", "sif-needs-info"))
        scan = await self.screen(inp, reply.text, "jira_ticket", reply.author)  # the answer becomes part of the ticket, so it is screened the same way
        if scan["verdict"] == "block":
            await self.emit(inp, "jira.bounced", text="The reply looks like an attempt to give the agent new instructions.")
            await self.jira(inp, f"{m} did not start: the reply looks like an attempt to instruct the agent. A person should review it.", add=["sif-blocked"], remove=["sif-picked"])
            return "bounced"
        await self.emit(inp, "agent.answered", text=f"{m} {reply.text}", actor=reply.author)
        await self.jira(inp, "", remove=["sif-needs-info"])
        self.stage = "classifying"
        return dataclasses.replace(inp, description=f"{inp.description}\n\nClarification from {reply.author or 'the reporter'}:\n{reply.text}")

    async def run_agent(self, inp: TaskInput, commit: str, context_md: str, profile: str, risk: str, role: str, description: str, documents: dict[str, str],
                        what: str = "") -> AgentResult | str:
        """One agent in its own sandbox. Returns its result, or the final outcome ('failed', 'killed', 'timed_out') if the run ended."""
        mention = inp.mention
        self.done, self.failed = None, None
        if self.killed_by:
            return await self.stopped(inp)
        self.stage = "sandbox"
        budget_left = max(round(self.budget - self.cost_base, 2), 0.01)  # the budget is for the whole ticket, not per agent
        multi = role != "developer" or bool(self.docs)
        if multi:
            await self.emit(inp, "stage.started", text=role)
        try:
            await self.act("start_sandbox", inp, commit, context_md, budget_left, profile, role, description, documents, timeout=LONG)
        except ActivityError as e:
            self.keep("sandbox", await self.act("stop_sandbox", inp.jira_key, retry=ONCE) or [])
            return await self.fail(inp, f"could not start a sandbox: {e.cause}")
        await self.emit(inp, "sandbox.created")
        await self.emit(inp, "agent.running")
        what = what or {"planner": "is working out how to split this", "intent": "is writing intent.md", "architect": "is writing design.md",
                        "developer": "started working on this ticket"}[role]
        follow = f" It will follow the {' and '.join(self.docs)}." if role == "developer" and self.docs else ""
        await self.note(inp, f"{mention} {what} ({profile}, risk {risk}, budget ${self.budget:.2f}).{follow}")

        try:
            outcome = await self.supervise(inp)
            return self.done if outcome == "done" else outcome
        finally:
            self.stage = "closing"
            self.cost_base, self.cost = self.cost_base + self.cost, 0.0  # the next sandbox starts counting from zero
            try:
                self.keep("sandbox", await self.act("stop_sandbox", inp.jira_key, retry=NET) or [])
            except ActivityError:
                workflow.logger.error("sandbox for %s was not stopped; the provisioner's reaper will remove it", inp.jira_key)

    async def document(self, inp: TaskInput, commit: str, context_md: str, profile: str, risk: str, role: str, name: str, needs_approval: bool) -> str | None:
        """Has an agent write one document, revising it on feedback if a person must approve it. None when it is ready; otherwise the final outcome."""
        feedback, rounds = "", 0
        while True:
            description = inp.description + (f"\n\nReviewer feedback:\n{feedback}" if feedback else "")
            result = await self.run_agent(inp, commit, context_md, profile, risk, role, description, dict(self.docs))
            if isinstance(result, str):
                return result
            text = result.documents.get(name, "").strip()
            if not text:
                return await self.fail(inp, f"could not finish: the {role} agent did not produce {name}.")
            self.docs[name] = text if needs_approval else UNREVIEWED + text
            if not needs_approval:
                await self.note(inp, f"{inp.mention} wrote {name} (no approval is needed; it ships with the pull request).")
                return None
            verdict, feedback = await self.await_approval(inp, name, text, final=rounds >= inp.max_revisions)
            if verdict == "approved":
                return None
            if verdict != "feedback":
                return verdict
            rounds += 1

    async def split(self, inp: TaskInput, policy, commit: str, context_md: str, profile: str, risk: str) -> str:
        """An epic or feature becomes child issues, after a person agrees to the split if the policy says so."""
        feedback, rounds, previous = "", 0, {}
        while True:
            description = inp.description + (f"\n\nReviewer feedback:\n{feedback}" if feedback else "")
            result = await self.run_agent(inp, commit, context_md, profile, risk, "planner", description, previous)
            if isinstance(result, str):
                return result
            raw = result.documents.get("split.json", "")
            children = H.parse_split(raw)
            if isinstance(children, str):
                return await self.fail(inp, f"could not split this: {children}.")
            if not policy.split_approval:
                break
            previous = {"split.json": raw}
            proposal = "\n\n".join(f"{i}. {c['title']}\n{c['description']}" for i, c in enumerate(children, 1))
            verdict, feedback = await self.await_approval(inp, "the proposed split", proposal, final=rounds >= inp.max_revisions)
            if verdict == "approved":
                break
            if verdict != "feedback":
                return verdict
            rounds += 1
        return await self.create_children(inp, policy, children)

    async def create_children(self, inp: TaskInput, policy, children: list[dict[str, str]]) -> str:
        project = inp.jira_key.split("-")[0]
        for c in children:
            body = f"{c['description']}\n\nRepo: {inp.repo}\n\nSplit from {inp.jira_key}."
            try:
                key = await workflow.execute_activity("jira_create_issue", args=[project, c["title"], body, policy.split_into, inp.jira_key], task_queue=JIRA_QUEUE,
                                                      start_to_close_timeout=SHORT, retry_policy=ONCE)  # no retry: a lost reply would leave a duplicate
            except ActivityError as e:
                made = f" Created before the error: {', '.join(self.children)}." if self.children else ""
                return await self.fail(inp, f"could not create '{c['title']}': {e.cause}.{made}")
            self.children.append(key)
        await self.emit(inp, "children.created", text=", ".join(self.children))
        await self.jira(inp, f"{inp.mention} created {len(self.children)} {policy.split_into} issues from this one: {', '.join(self.children)}. They are picked up on the next poll.",
                        add=["sif-split"], remove=["sif-picked"])
        return "split"

    @staticmethod
    def shorten(text: str) -> str:
        return text if len(text) <= JIRA_COMMENT_CHARS else text[:JIRA_COMMENT_CHARS] + "\n\n(shortened here; the whole text is kept for the next step)"

    async def await_approval(self, inp: TaskInput, label: str, text: str, final: bool) -> tuple[str, str]:
        """Posts a document on Jira and waits for a person: approve, stop, or feedback. Returns (verdict, feedback) with verdict
        'approved', 'feedback', 'killed' or 'timed_out'. When final, feedback is no longer taken: only approve or stop."""
        m = inp.mention
        waited_since = len(self.replies)  # taken before any await: only replies that arrive from now on count
        deadline_ms = self.ts() + inp.wait_timeout_seconds * 1000
        await self.emit(inp, "agent.asked_question", text=f"Please review {label} on Jira:\n\n{text[:APPROVAL_EVENT_CHARS]}", reason="approval", deadline=deadline_ms)
        hint = f"Reply `{m} approve` to accept it or `{m} stop` to cancel." if final else f"Reply `{m} approve` to accept it, `{m} stop` to cancel, or `{m} <what to change>` and I will revise it."
        await self.jira(inp, f"{m} needs your review of {label} for {inp.jira_key}:\n\n{self.shorten(text)}\n\n{hint}")
        self.stage = "waiting_for_human"  # only now: the request is visible to people
        while True:
            try:
                await workflow.wait_condition(lambda: len(self.replies) > waited_since or bool(self.killed_by), timeout=timedelta(seconds=inp.wait_timeout_seconds))
            except TimeoutError:
                await self.emit(inp, "wait.timed_out", text=f"No one reviewed {label} within {inp.wait_timeout_seconds // 60} minutes.")
                await self.jira(inp, f"{m} stopped waiting after {inp.wait_timeout_seconds // 60} minutes. Add the label again to restart.", add=["sif-timed-out"], remove=["sif-picked"])
                return "timed_out", ""
            if self.killed_by:
                return await self.stopped(inp), ""
            reply = self.replies[waited_since]
            waited_since += 1
            decision = classify_reply(reply.text)
            if decision is Decision.APPROVE:
                await self.emit(inp, "agent.answered", text=f"{m} {reply.text}", actor=reply.author)
                await self.note(inp, f"{m} {label} approved by {reply.author or 'a reviewer'}.")
                return "approved", ""
            if decision is Decision.STOP:
                return await self.stopped(inp, reply.author), ""
            if final:
                await self.jira(inp, f"{m} no more revisions: reply `{m} approve` or `{m} stop`.")
                continue
            if workflow.patched(INCIDENTS) and await self.refused(inp, reply):
                continue
            await self.emit(inp, "agent.answered", text=f"{m} {reply.text}", actor=reply.author)
            await self.note(inp, f"{m} revising {label} with feedback from {reply.author or 'a reviewer'}.")
            return "feedback", reply.text

    async def supervise(self, inp: TaskInput) -> str:
        self.stage = "running"
        deadline = workflow.now() + timedelta(seconds=inp.max_run_seconds)
        while True:
            remaining = deadline - workflow.now()
            try:
                await workflow.wait_condition(self.workable, timeout=max(remaining, timedelta(seconds=1)))
            except TimeoutError:
                return await self.fail(inp, f"stopped: the run exceeded its {inp.max_run_seconds // 60}-minute limit.")
            await self.flush(inp)
            if self.killed_by:
                return await self.stopped(inp)
            if self.failed is not None:
                return await self.fail(inp, f"could not finish: {self.failed or 'the agent reported a failure.'}")
            if self.done:
                return "done"
            pending = [q for q in self.asked if q not in self.answers]
            if pending:
                outcome = await self.ask_human(inp, self.questions[pending[0]])
                if outcome:
                    return outcome

    async def ask_human(self, inp: TaskInput, q: Question) -> str | None:
        """Hands control to Jira: post the question, then wait for an allowlisted human's reply, at most wait_timeout_seconds."""
        waited_since = len(self.replies)  # taken before any await: only replies that arrive from now on can answer this question
        deadline_ms = self.ts() + inp.wait_timeout_seconds * 1000
        await self.emit(inp, "agent.asked_question", text=q.text, reason=q.reason, deadline=deadline_ms)
        hint = f"Reply with `{inp.mention} approve` to add ${inp.budget_extension_usd:.2f}, or `{inp.mention} stop`." if q.reason == "budget" else f"Reply with `{inp.mention} <your answer>`."
        await self.jira(inp, f"{inp.mention} needs input on {inp.jira_key}:\n\n{q.text}\n\n{hint}")
        self.stage = "waiting_for_human"  # only now: the question is visible to people

        while q.id not in self.answers:
            try:
                await workflow.wait_condition(lambda: len(self.replies) > waited_since or bool(self.killed_by) or bool(self.failed), timeout=timedelta(seconds=inp.wait_timeout_seconds))
            except TimeoutError:
                self.answers[q.id] = Answer(status="timed_out", text="No reply arrived in time; the sandbox was stopped.")
                await self.emit(inp, "wait.timed_out", text=f"No reply within {inp.wait_timeout_seconds // 60} minutes.")
                await self.jira(inp, f"{inp.mention} stopped waiting after {inp.wait_timeout_seconds // 60} minutes and shut the sandbox down. Add the label again to restart.", add=["sif-timed-out"], remove=["sif-picked"])
                return "timed_out"
            if self.killed_by or self.failed:
                self.answers[q.id] = Answer(status="stopped", text="The run was ended.")
                self.stage = "running"
                return None  # supervise reports it
            reply = self.replies[waited_since]
            waited_since += 1
            decision = classify_reply(reply.text)
            if q.reason == "budget":
                if decision is Decision.APPROVE:
                    self.budget = round(self.budget + inp.budget_extension_usd, 2)
                    await self.act("set_budget", inp.jira_key, round(self.budget - self.cost_base, 2))
                    self.answers[q.id] = Answer(status="answered", text="approved", approved_budget_usd=self.budget)
                elif decision is Decision.STOP:
                    self.answers[q.id] = Answer(status="stopped", text="A human replied stop.")
                else:
                    await self.jira(inp, f"{inp.mention} needs a decision on the budget: reply `{inp.mention} approve` or `{inp.mention} stop`.")
                    continue
            elif decision is Decision.STOP:
                self.answers[q.id] = Answer(status="stopped", text="A human replied stop.")
            elif workflow.patched(INCIDENTS) and await self.refused(inp, reply):
                continue
            else:
                self.answers[q.id] = Answer(status="answered", text=reply.text)
            await self.emit(inp, "agent.answered", text=f"{inp.mention} {reply.text}", budgetUsd=self.answers[q.id].approved_budget_usd, actor=reply.author)
        self.stage = "running"
        if self.answers[q.id].status == "stopped":
            await self.emit(inp, "task.killed")
            await self.jira(inp, f"{inp.mention} stopped as requested.", remove=["sif-picked"])
            return "killed"
        return None

    async def publish(self, inp: TaskInput, commit: str, r: AgentResult, profile: str = "", risk: str = "") -> str:
        self.stage = "publishing"
        branch = f"sif/{inp.jira_key}-{slug(inp.title)}"
        docs = f"\n\nDocuments written for this change: {', '.join(self.docs)} (under docs/sif/{inp.jira_key}/)." if self.docs else ""
        footer = f"{docs}\n\n---\nOpened by sif for {inp.jira_key}. This is a draft: a person must review and merge it."
        if risk == "high":  # triage saw auth, payments, migrations, deletes or production in the ticket
            footer = f"\n\n> **High-risk change** (security, money, data or production per triage): review with extra care.{footer}"
        await self.emit(inp, "publish.started", text=branch)
        try:
            pr = await self.act("publish_pr", inp.repo, commit, branch, r.patch, r.title or f"{inp.jira_key} {inp.title}", (r.body or r.summary) + footer, inp.base_branch, inp.jira_key, inp.team, timeout=LONG)
        except ActivityError as e:
            return await self.fail(inp, f"finished the change but it could not be published: {e.cause}")
        await self.emit(inp, "pr.opened", prUrl=pr["pr_url"], costUsd=round(self.spent(), 4))
        watching = (f"\n\nI will keep working on it until it is merged or closed: request changes on the PR (or mention {inp.mention} in a PR comment) "
                    "and I will revise it; failing CI is fixed the same way.") if inp.review_poll_seconds > 0 else ""
        await self.jira(inp, f"{inp.mention} opened a draft pull request: {pr['pr_url']}\n\n{r.summary[:800]}{watching}")
        if inp.review_poll_seconds > 0:
            workflow.continue_as_new(ReviewInput(task=inp, pr_number=pr["number"], pr_url=pr["pr_url"], branch=branch, budget_usd=self.budget, spent_usd=round(self.spent(), 4),
                                                 docs=dict(self.docs), profile=profile, risk=risk, base_commit=commit), workflow=REVIEW_WORKFLOW)
        return "pr_opened"


@workflow.defn(name=REVIEW_WORKFLOW)
class ReviewWorkflow(TaskWorkflow):
    """The second half of a task: its draft PR is open, and the factory keeps it until a person merges or closes it.

    Every review_poll_seconds it reads the PR back through git-broker. A review that requests changes, a PR comment that mentions the bot,
    or failing CI on the head commit becomes one revision round: a developer agent works from the PR branch and pushes a fast-forward
    commit onto it. Reviewers only review; they never have to pick the branch up themselves unless max_review_rounds runs out.
    Runs under the same workflow id as TaskWorkflow (continue-as-new), so Jira replies and kills still reach it, but as its own type so a PR
    waiting days for review does not hold one of the concurrency slots."""

    @workflow.run
    async def run(self, r: ReviewInput) -> str:  # type: ignore[override]
        inp, m = r.task, r.task.mention
        self.budget, self.cost_base, self.reported_cost, self.docs = r.budget_usd, r.spent_usd, r.spent_usd, dict(r.docs)
        self.stage = "in_review"
        seen: set[str] = set()
        rounds, told_limit = 0, False
        deadline = workflow.now() + timedelta(seconds=inp.review_watch_seconds)
        while True:
            with contextlib.suppress(TimeoutError):  # the poll interval passed
                await workflow.wait_condition(lambda: bool(self.killed_by), timeout=timedelta(seconds=inp.review_poll_seconds))
            if self.killed_by or workflow.now() >= deadline:
                why = f"was asked to stop by {self.killed_by}" if self.killed_by else f"stopped watching {r.pr_url} after {inp.review_watch_seconds // 86400} days without a decision"
                await self.emit(inp, "review.stopped", prUrl=r.pr_url, actor=self.killed_by or None,
                                text=f"Stopped by {self.killed_by}." if self.killed_by else f"No decision on the PR within {inp.review_watch_seconds // 86400} days.")
                await self.jira(inp, f"{m} {why}. The pull request stays open for a person to finish.", remove=["sif-picked"])
                return "pr_opened"
            try:
                st = await self.act("pr_status", inp.repo, r.pr_number, inp.team, m)
            except ActivityError as e:
                workflow.logger.warning("could not read %s: %s", r.pr_url, e.cause)
                continue
            if st["state"] in ("merged", "closed"):
                fixups = st.get("fixups") or []  # commits people added before merging: what the agent did not get right, for the improvement cycle
                extra: dict = {"fixups": len(fixups)} if st["state"] == "merged" else {}
                if fixups:
                    extra["text"] = "Merged with commits by people:\n" + "\n".join(f"- {f['message']} ({f['author']})" for f in fixups[:20])
                await self.emit(inp, f"pr.{st['state']}", prUrl=r.pr_url, costUsd=round(self.spent(), 4), actor=st.get("merged_by") or None, **extra)
                if st["state"] == "merged" and r.base_commit:  # what people merged becomes a replay case for judging future lessons
                    try:
                        await self.act("record_eval_case", inp.repo, r.base_commit, st["head_sha"], inp.team, inp.jira_key, inp.title, inp.description, timeout=LONG, retry=ONCE)
                    except ActivityError as e:
                        workflow.logger.warning("could not keep %s as a replay case: %s", inp.jira_key, e.cause)
                text = f"{m} {r.pr_url} was merged. Done." if st["state"] == "merged" else f"{m} {r.pr_url} was closed without merging."
                await self.jira(inp, text, add=[f"sif-{st['state']}"], remove=["sif-picked"])
                return st["state"]

            new = [Feedback(id=f["id"], author=f["author"], body=f["body"], kind=f["kind"], path=f.get("path", "")) for f in st["feedback"] if f["id"] not in seen]
            ci = st["ci"]
            if ci["state"] == "failure" and f"ci-{ci['sha']}" not in seen:
                new.append(Feedback(id=f"ci-{ci['sha']}", author="CI", kind="ci", body="\n\n".join(f"{x['name']} failed. {x['summary']}".strip() for x in ci["failures"])))
            if not new:
                continue
            seen |= {f.id for f in new}
            items = await self.screened(inp, r, new)
            if not items:
                continue
            if rounds >= inp.max_review_rounds:
                if not told_limit:
                    told_limit = True
                    note = f"I have made {rounds} revisions, the limit for one pull request. Please finish the remaining changes by hand."
                    await self.comment(inp, r, note)
                    await self.jira(inp, f"{m} {note} ({r.pr_url})")
                continue
            rounds += 1
            if await self.revise(inp, r, st["head_sha"], items, rounds) == "killed":
                return "killed"

    async def screened(self, inp: TaskInput, r: ReviewInput, items: list[Feedback]) -> list[Feedback]:
        """PR comments are text from people with repository access, but they still reach the agent: scan them like ticket text."""
        keep = []
        for f in items:
            scan = await self.screen(inp, f.body, "pr_review", f.author)
            if scan["verdict"] == "block":
                await self.comment(inp, r, f"I ignored a comment by {f.author}: it looks like an attempt to instruct the agent ({', '.join(scan['rules'][:3])}).")
            else:
                keep.append(f)
        return keep

    async def comment(self, inp: TaskInput, r: ReviewInput, body: str) -> None:
        try:
            await self.act("pr_comment", inp.repo, r.pr_number, body, inp.team)
        except ActivityError:
            workflow.logger.warning("could not comment on %s", r.pr_url)

    async def revise(self, inp: TaskInput, r: ReviewInput, head: str, items: list[Feedback], round_no: int) -> str:
        m = inp.mention
        asked = "\n\n".join(f"{'CI' if f.kind == 'ci' else f'Review from {f.author}'}{f' ({f.path})' if f.path else ''}:\n{f.body}" for f in items)
        self.budget = max(self.budget, round(self.cost_base + inp.budget_extension_usd, 2))  # each round may spend up to one budget extension
        who = ", ".join(sorted({f.author for f in items}))
        await self.emit(inp, "review.received", text=asked[:2000], budgetUsd=self.budget, actor=who)
        await self.note(inp, f"{m} is revising {r.pr_url} (round {round_no} of {inp.max_review_rounds}) for {who}. Budget for this ticket is now ${self.budget:.2f}.")
        try:
            commit = (await self.act("checkout_repo", inp.repo, r.branch, inp.team, timeout=LONG))["commit"]
            context_md = await self.context(inp)
        except ActivityError as e:
            return await self.unresolved(inp, r, f"I could not fetch {r.branch} to revise it: {e.cause}")
        if commit != head:
            workflow.logger.info("%s moved from %s to %s since it was read; revising the newer head", r.branch, head[:8], commit[:8])
        description = (f"{inp.description}\n\nThis change is already open as pull request {r.pr_url}, and you are working on its branch. "
                       f"Make the changes asked for below, and nothing else:\n\n{asked}")
        result = await self.run_agent(inp, commit, context_md, r.profile or "cheap-best", r.risk or "medium", "developer", description, dict(self.docs),
                                      what=f"is addressing review feedback on {r.pr_url}")
        if result == "killed":
            return "killed"
        if isinstance(result, str):
            return await self.unresolved(inp, r, f"The revision for round {round_no} ended as {result.replace('_', ' ')}; the PR is unchanged.")
        if not result.patch.strip():
            return await self.unresolved(inp, r, f"I looked at the feedback and changed nothing: {result.summary[:1500] or 'no reason given'}")
        await self.emit(inp, "publish.started", text=r.branch)
        try:
            pushed = await self.act("update_pr", inp.repo, commit, r.branch, result.patch, f"{inp.jira_key}: address review feedback (round {round_no})", r.pr_number,
                                    f"Revision {round_no} pushed for {who}:\n\n{result.summary[:3000]}", inp.team, timeout=LONG)
        except ActivityError as e:
            return await self.unresolved(inp, r, f"I made a revision but could not push it: {e.cause}")
        await self.emit(inp, "pr.updated", prUrl=r.pr_url, text=f"Pushed {pushed['commit'][:8]}: {result.summary[:500]}", costUsd=round(self.spent(), 4))
        await self.note(inp, f"{m} pushed revision {round_no} to {r.pr_url}.")  # the summary is on the PR, where the reviewer is
        self.stage = "in_review"
        return "updated"

    async def unresolved(self, inp: TaskInput, r: ReviewInput, why: str) -> str:
        await self.comment(inp, r, why)
        await self.emit(inp, "pr.updated", prUrl=r.pr_url, text=why[:500], costUsd=round(self.spent(), 4))
        await self.note(inp, f"{inp.mention} {why} ({r.pr_url})")  # said on the PR, where the reviewer is
        self.stage = "in_review"
        return "unresolved"


@workflow.defn(name=REPLAY_WORKFLOW)
class ReplayWorkflow(TaskWorkflow):
    """A past ticket, replayed: a developer agent starts from the ticket's base commit, with or without one lesson in its rules, and its
    patch is returned instead of published. Nobody is waiting on Jira, so questions get a standing answer and nothing is posted or
    emitted to the dashboard. The sandbox, budget cap and time limit are the same as for real work."""

    NO_HUMAN = "No person is available: this is a replay of a past ticket. Make the most reasonable choice, say which in your summary, and finish."

    @workflow.run
    async def run(self, r: ReplayInput) -> dict:  # type: ignore[override]
        inp = r.task
        self.budget = inp.budget_usd
        try:
            commit = (await self.act("checkout_repo", inp.repo, r.base_commit, inp.team, timeout=LONG))["commit"]
        except ActivityError as e:
            return {"outcome": "failed", "patch": "", "cost": 0.0, "error": str(e.cause)[:300]}
        include, exclude = (r.lesson_id, None) if r.with_lesson else (None, r.lesson_id)
        context_md = await self.act("build_context", inp.jira_key, inp.title, inp.description, inp.team, inp.repo, include, exclude, retry=ONCE)
        result = await self.run_agent(inp, commit, context_md, "cheap-best", "medium", "developer", inp.description, {})
        if isinstance(result, str):
            return {"outcome": result, "patch": "", "cost": round(self.spent(), 4)}
        return {"outcome": "done", "patch": result.patch, "summary": result.summary[:500], "cost": round(self.spent(), 4)}

    async def emit(self, inp: TaskInput, type: str, **fields) -> None:
        return None

    async def jira(self, inp: TaskInput, text: str, add: list[str] | None = None, remove: list[str] | None = None) -> None:
        return None

    async def ask_human(self, inp: TaskInput, q: Question) -> str | None:
        if q.reason == "budget":
            self.answers[q.id] = Answer(status="stopped", text="The replay budget is used up.")
            return "failed"
        self.answers[q.id] = Answer(status="answered", text=self.NO_HUMAN)
        return None


@workflow.defn(name=EVAL_WORKFLOW)
class EvalWorkflow:
    """Replays merged tickets twice, with and without a lesson, scores each patch against what was merged, and reports the comparison
    to portal-api, which records it as the lesson's eval (learn.passes_gate). Both arms of a case run side by side; cases run one after
    another, so a run never holds more than two sandboxes."""

    @workflow.run
    async def run(self, e: EvalInput) -> dict:
        rows, base_pass, cand_pass, cost = [], 0, 0, 0.0
        for i, case in enumerate(e.cases):
            arms = await asyncio.gather(self.replay(e, i, case, True), self.replay(e, i, case, False))
            (with_, s_with), (without, s_without) = [(a, replay.score(case.reference, a.get("patch", ""))) for a in arms]
            cand_pass += s_with >= e.pass_score
            base_pass += s_without >= e.pass_score
            cost += with_.get("cost", 0.0) + without.get("cost", 0.0)
            rows.append({"task_id": case.task_id, "with": s_with, "without": s_without, "with_outcome": with_["outcome"], "without_outcome": without["outcome"]})
        result = {"n": len(e.cases), "baseline_pass": base_pass, "candidate_pass": cand_pass, "cost_usd": round(cost, 4), "cases": rows}
        try:
            await workflow.execute_activity("report_eval", args=[e.run_id, result], task_queue=ORCHESTRATOR_QUEUE, start_to_close_timeout=SHORT, retry_policy=NET)
        except ActivityError:
            workflow.logger.error("could not report replay run %s", e.run_id)
        return result

    async def replay(self, e: EvalInput, i: int, case: EvalCase, with_lesson: bool) -> dict:
        key = f"eval{e.run_id}-{i}-{'with' if with_lesson else 'without'}"
        task = TaskInput(jira_key=key, title=case.title, description=case.description, team=case.team, repo=case.repo, budget_usd=e.budget_usd,
                         budget_extension_usd=0.0, max_run_seconds=e.max_run_seconds, wait_timeout_seconds=60)
        try:
            return await workflow.execute_child_workflow(REPLAY_WORKFLOW, ReplayInput(task=task, base_commit=case.base_commit, lesson_id=e.lesson_id, with_lesson=with_lesson),
                                                         id=f"task-{key}", task_queue=ORCHESTRATOR_QUEUE)
        except Exception as err:  # noqa: BLE001 - one broken replay counts as a failed attempt, not a failed run
            return {"outcome": "failed", "patch": "", "cost": 0.0, "error": str(err)[:300]}
