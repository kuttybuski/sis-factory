"""Agent adapters. The runner only knows this interface, so Claude Code, an open-source agent, or the scripted demo agent are interchangeable."""

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent_runner.gateway import BudgetPause, Gateway, Task


@dataclass
class Outcome:
    summary: str = ""
    title: str = ""
    body: str = ""


class Adapter(Protocol):
    async def run(self, gw: Gateway, task: Task, repo: Path, context_file: Path) -> Outcome: ...


# ------------------------------------------------------------------------------------ scripted demo agent
_DIRECTIVE = re.compile(r"^(edit\s+\S+:|ask:|llm:|fail:|story:)", re.IGNORECASE)
_MARKER = re.compile(r"^(</?untrusted\b|-----(BEGIN|END) UNTRUSTED )")  # injection_guard's wrapping around the ticket text
_EDIT = re.compile(r'^\s*edit\s+(\S+?):\s*replace\s+"(.*?)"\s+with\s+"(.*?)"\s*$', re.IGNORECASE)


class ScriptedAdapter:
    """Follows simple directives in the ticket description. Used for tests and demos: no model, no key, fully deterministic.

        edit src/app.py: replace "a + b" with "b + a"
        ask: Which style should we use?
        llm: 3        (makes 3 model calls through the gateway, to exercise budgets)
        fail: reason
        story: Add backoff    (planner role: one child issue per line)

    The planner, intent and architect roles write their document from the ticket text instead of editing code.
    """

    def __init__(self, model: str = "scripted") -> None:
        self.model = model

    async def run(self, gw: Gateway, task: Task, repo: Path, context_file: Path) -> Outcome:
        notes, skipped = [], []
        await gw.log([f"scripted agent started on {task.task_id}", f"context: {len(context_file.read_text().splitlines())} lines"])
        for raw in task.description.splitlines():
            line = raw.strip()
            if not line or _MARKER.match(line):
                continue
            if m := _EDIT.match(line):
                if task.role != "developer":
                    continue
                path, old, new = m.groups()
                f = (repo / path).resolve()
                if not str(f).startswith(str(repo.resolve())) or not f.is_file():
                    raise RuntimeError(f"cannot edit {path}")
                f.write_text(f.read_text().replace(old, new))
                await gw.log([f"Edit {path}"])
                notes.append(f"edited {path}")
            elif line.lower().startswith("ask:"):
                await gw.log([f"Asking a human: {line[4:].strip()}"])
                answer = await gw.ask(line[4:].strip())
                await gw.log([f"Answer received: {answer}"])
                notes.append(f"asked a question; answer: {answer}")
            elif line.lower().startswith("llm:"):
                for i in range(int(line[4:])):
                    while True:
                        try:
                            await gw.llm(self.model, f"step {i}")
                            break
                        except BudgetPause as pause:
                            await gw.log(["Budget reached; waiting for a decision"])
                            await gw.wait_for_budget_decision() if pause.ask_id is None else await gw.wait_for({"id": pause.ask_id, "status": "pending"})
            elif line.lower().startswith("fail:"):
                raise RuntimeError(line[5:].strip())
            elif task.role == "developer" and not _DIRECTIVE.match(line):
                skipped.append(line)
        if skipped:  # say what was not acted on: the demo agent only follows directives, and a plain-English ticket is all skipped
            await gw.log([f"Not a directive, skipped: {line[:200]}" for line in skipped[:20]]
                         + ['The scripted agent only follows lines such as: edit src/app.py: replace "a" with "b"'])
        if task.role != "developer":
            name = self.write_document(task, repo)
            notes.append(f"wrote {name}")
        return Outcome(summary="; ".join(notes) or "no changes requested", title=f"{task.task_id} {task.title}", body=f"Automated change for {task.task_id}.\n\n" + "\n".join(f"- {n}" for n in notes))


    @staticmethod
    def write_document(task: Task, repo: Path) -> str:
        name = task.output_file
        text = task.description
        feedback = text.split("Reviewer feedback:", 1)[1].strip() if "Reviewer feedback:" in text else ""
        body = [ln for ln in text.split("Reviewer feedback:")[0].splitlines() if ln.strip() and not _DIRECTIVE.match(ln.strip())]
        if task.role == "planner":
            titles = [ln.strip()[6:].strip() for ln in text.splitlines() if ln.strip().lower().startswith("story:")] or [task.title]
            content = json.dumps([{"title": t, "description": f"{t}.\n\nAcceptance criteria: {t} works as described in {task.task_id}."} for t in titles], indent=2)
        elif task.role == "intent":
            content = f"# Intent: {task.title}\n\n" + "\n".join(body) + (f"\n\nRevised after feedback: {feedback}" if feedback else "") + "\n"
        else:
            intent = task.documents.get("intent.md", "")
            content = (f"# Design: {task.title}\n\n" + ("Built on the intent:\n\n" + intent + "\n" if intent else "") + "\n".join(body)
                       + (f"\n\nRevised after feedback: {feedback}" if feedback else "") + "\n")
        (repo / name).write_text(content)
        return name


# ------------------------------------------------------------------------------------ Claude Code
SYSTEM_SUFFIX = ("When you need a decision or information a human has, run `sif-ask \"<your question>\"` and wait for the answer; do not guess. "
                 "Stay within the ticket. Never change CI configuration or CODEOWNERS. Run the project's tests before you finish if you changed code.")


class ClaudeCodeAdapter:
    """Runs the Claude Code CLI headless against the gateway. Claude Code is proprietary (Anthropic's terms apply) and is only
    installed in the image when built with INSTALL_CLAUDE=true. UNVERIFIED end to end: command-line flags and environment variables
    follow the CLI's documentation and must be checked against the installed version."""

    def __init__(self, gateway_url: str, token: str, model: str, binary: str = "claude") -> None:
        self.gateway_url, self.token, self.model, self.binary = gateway_url, token, model, binary

    def command(self, task: Task, context_file: Path) -> list[str]:
        head = f"Task {task.task_id}: {task.title}\n\n{task.description}\n\nBackground context is in {context_file}. Read it first. "
        if task.role == "developer":
            prompt = head + (f"Documents from earlier stages, if any, are in {task.docs_dir}/. " if task.documents else "") + "Make the change in the current directory; do not commit."
            tools = "Read,Edit,Write,Glob,Grep,Bash"
        else:  # a document role writes exactly one file: anything else it changes is discarded, because only the document is sent back
            prompt = head + (f"Earlier documents are in {task.docs_dir}/; if a previous draft of {task.output_file} is there, revise it using the reviewer feedback above. "
                             if task.documents else "") + f"Write {task.output_file} in the repository root and change nothing else."
            tools = "Read,Write,Glob,Grep,Bash"  # Bash so it can still run sif-ask
        return [self.binary, "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", self.model, "--permission-mode", "acceptEdits",
                "--allowedTools", tools, "--append-system-prompt", f"{task.guidance}\n\n{SYSTEM_SUFFIX}"]

    def environment(self) -> dict[str, str]:
        return {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/workspace"), "ANTHROPIC_BASE_URL": self.gateway_url,
                "ANTHROPIC_AUTH_TOKEN": self.token, "SIF_GATEWAY_URL": self.gateway_url, "SIF_TASK_TOKEN": self.token,
                "DISABLE_TELEMETRY": "1", "DISABLE_AUTOUPDATER": "1", "DISABLE_ERROR_REPORTING": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}

    async def run(self, gw: Gateway, task: Task, repo: Path, context_file: Path) -> Outcome:
        if shutil.which(self.binary) is None:
            raise RuntimeError("the claude CLI is not installed in this image (build with INSTALL_CLAUDE=true)")
        proc = await asyncio.create_subprocess_exec(*self.command(task, context_file), cwd=repo, env=self.environment(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        final = ""
        batch: list[str] = []
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            text = describe(line)
            if text:
                batch.append(text)
            if len(batch) >= 10:
                await gw.log(batch); batch = []
            try:
                ev = json.loads(line)
                if ev.get("type") == "result":
                    final = str(ev.get("result", ""))
            except ValueError:
                pass
        await gw.log(batch)
        if await proc.wait():
            if (await gw.budget())["exceeded"]:
                await gw.wait_for_budget_decision()
                raise BudgetPause(None)  # the runner restarts the agent after a human approves more budget
            raise RuntimeError("the agent exited with an error")
        return Outcome(summary=final[:2000], title=f"{task.task_id} {task.title}", body=final[:4000])


def describe(stream_json_line: str) -> str:
    """One readable log line from a Claude Code stream-json event; empty for events not worth showing."""
    try:
        ev = json.loads(stream_json_line)
    except ValueError:
        return stream_json_line[:300]
    if ev.get("type") == "assistant":
        for block in (ev.get("message") or {}).get("content", []):
            if block.get("type") == "tool_use":
                inp = block.get("input") or {}
                return f"{block.get('name')}: {inp.get('file_path') or inp.get('command') or inp.get('pattern') or ''}"[:300]
            if block.get("type") == "text" and block.get("text", "").strip():
                return block["text"].strip().splitlines()[0][:300]
    return ""


def make_adapter(kind: str, gateway_url: str, token: str, model: str) -> Adapter:
    if kind == "scripted":
        return ScriptedAdapter(model)
    if kind == "claude":
        return ClaudeCodeAdapter(gateway_url, token, model)
    raise ValueError(f"unknown agent '{kind}': use 'scripted' or 'claude'")
