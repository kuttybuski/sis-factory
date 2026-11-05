import asyncio
import os
import sys
from pathlib import Path

from agent_runner.adapters import make_adapter
from agent_runner.gateway import BudgetPause, Gateway, Stopped, Task
from agent_runner.workspace import make_patch, place_documents, prepare, read_document

MAX_RESTARTS = 5


def changes(patch: str) -> list[str]:
    """One log line per changed file, with lines added and removed: what the agent hands back, before it goes to git-broker."""
    out, name, add, rem = [], None, 0, 0
    for line in patch.splitlines() + ["diff --git END"]:
        if line.startswith("diff --git "):
            if name:
                out.append(f"Changed {name} (+{add} -{rem})")
            name, add, rem = line.split(" b/", 1)[-1] if " b/" in line else None, 0, 0
        elif line.startswith("+") and not line.startswith("+++"):
            add += 1
        elif line.startswith("-") and not line.startswith("---"):
            rem += 1
    return [f"Handing back {len(out)} changed file(s)", *out[:50]]


async def run(gw: Gateway, agent: str, root: Path, gateway_url: str, token: str, model: str) -> int:
    task = await gw.task()
    await gw.log([f"{task.task_id}: fetched task and context"])
    repo = await prepare(await gw.archive(), root)
    files = sum(1 for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts)
    await gw.log([f"Workspace ready: {files} files from {task.repo or 'the repository'} at {task.commit[:8] or 'its base commit'}, agent '{agent}' ({model})"])
    context_file = root / "CONTEXT.md"
    context_file.write_text(task.context_md)
    place_documents(repo, task.docs_dir, task.documents)  # the developer finds intent.md and design.md next to the code it changes
    adapter = make_adapter(agent, gateway_url, token, model)
    try:
        for _ in range(MAX_RESTARTS):
            try:
                outcome = await adapter.run(gw, task, repo, context_file)
                break
            except BudgetPause:  # a human approved more budget (or the pause was answered): carry on
                await gw.log(["Budget decision received; resuming"])
        else:
            raise RuntimeError("the agent kept hitting its budget")
        if task.role != "developer" and task.output_file:
            return await finish_document(gw, task, repo, outcome)
        patch = await make_patch(repo)
        if not patch.strip():
            demo = (" This is the demo agent (SIF_AGENT=scripted): it only follows lines such as `edit src/app.py: replace \"a\" with \"b\"`."
                    " Set SIF_AGENT=claude for real work.") if agent == "scripted" else ""
            await gw.fail("The agent finished without changing any files." + demo)
            return 1
        await gw.log(changes(patch))
        await gw.result(patch, outcome.summary, outcome.title, outcome.body)
        await gw.log(["Result submitted"])
        return 0
    except Stopped as e:
        await gw.fail(f"Stopped: {e}")
        return 2
    except Exception as e:
        await gw.fail(f"{type(e).__name__}: {e}")
        return 1


async def finish_document(gw: Gateway, task: Task, repo: Path, outcome) -> int:
    """The planner, intent and architect roles hand back one document rather than a code change."""
    text = read_document(repo, task.output_file, task.docs_dir)
    if not text.strip():
        await gw.fail(f"The {task.role} agent finished without writing {task.output_file}.")
        return 1
    await gw.result("", outcome.summary, outcome.title, outcome.body, {task.output_file: text})
    await gw.log([f"{task.output_file} submitted"])
    return 0


def main() -> None:
    url, token = os.environ["SIF_GATEWAY_URL"], os.environ["SIF_TASK_TOKEN"]
    gw = Gateway(url, token)
    code = asyncio.run(run(gw, os.environ.get("SIF_AGENT", "scripted"), Path(os.environ.get("SIF_WORKSPACE", "/workspace")), url, token, os.environ.get("SIF_MODEL_IMPLEMENTATION", "claude-haiku-5-5")))
    sys.exit(code)


def ask_main() -> None:
    """`sif-ask "question"`: asks a human through Jira and prints the answer. The agent calls this from its shell tool."""
    if len(sys.argv) < 2:
        print('usage: sif-ask "your question"', file=sys.stderr)
        sys.exit(64)
    gw = Gateway(os.environ["SIF_GATEWAY_URL"], os.environ["SIF_TASK_TOKEN"])
    try:
        print(asyncio.run(gw.ask(" ".join(sys.argv[1:]))))
    except Stopped as e:
        print(f"STOP: {e}. End your work and report what you have.", file=sys.stderr)
        sys.exit(3)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
