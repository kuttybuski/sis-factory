"""The sandbox's only way out. It holds a per-task token and the gateway address; nothing else."""

import asyncio
from dataclasses import dataclass, field

import httpx


class BudgetPause(Exception):
    def __init__(self, ask_id: str | None) -> None:
        super().__init__("budget reached")
        self.ask_id = ask_id


class Stopped(Exception):
    """A human replied 'stop', or the wait timed out: the run must end."""


@dataclass
class Task:
    task_id: str
    title: str
    description: str
    context_md: str
    repo: str
    commit: str
    guidance: str
    canary: str
    role: str = "developer"  # planner | intent | architect | developer
    documents: dict[str, str] = field(default_factory=dict)  # intent.md / design.md from earlier stages
    output_file: str = ""  # the one file a non-developer role must write (the gateway decides, so the sandbox needs no shared library)
    docs_dir: str = ""  # where documents from earlier stages live in the repository


class Gateway:
    def __init__(self, base_url: str, token: str, client: httpx.AsyncClient | None = None, poll_seconds: float = 3.0, ask_timeout: float = 8 * 3600) -> None:
        self.http = client or httpx.AsyncClient(base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=120)
        self.poll, self.ask_timeout = poll_seconds, ask_timeout

    async def task(self) -> Task:
        r = await self.http.get("/v1/task")
        r.raise_for_status()
        data = r.json()
        return Task(**{k: data[k] for k in Task.__dataclass_fields__ if k in data})

    async def archive(self) -> bytes:
        r = await self.http.get("/v1/archive")
        r.raise_for_status()
        return r.content

    async def ask(self, question: str) -> str:
        """Asks a human (through Jira) and waits for the reply. Raises Stopped if the human says stop or nobody answers in time."""
        r = await self.http.post("/v1/ask", json={"question": question})
        if r.status_code >= 400:
            raise RuntimeError(r.json().get("detail", r.text))
        return await self.wait_for(r.json())

    async def wait_for(self, answer: dict) -> str:
        qid, waited = answer["id"], 0.0
        while answer["status"] == "pending":
            if waited >= self.ask_timeout:
                raise Stopped("no answer arrived in time")
            await asyncio.sleep(self.poll)
            waited += self.poll
            answer = (await self.http.get(f"/v1/ask/{qid}")).json()
        if answer["status"] in ("timed_out", "stopped"):
            raise Stopped(answer.get("text") or answer["status"])
        return answer["text"]

    async def budget(self) -> dict:
        return (await self.http.get("/v1/budget")).json()

    async def wait_for_budget_decision(self) -> None:
        b = await self.budget()
        if b["exceeded"] and b["ask_id"]:
            await self.wait_for({"id": b["ask_id"], "status": "pending"})

    async def llm(self, model: str, prompt: str) -> dict:
        r = await self.http.post("/v1/messages", json={"model": model, "max_tokens": 256, "messages": [{"role": "user", "content": prompt}]})
        if r.status_code == 402:
            raise BudgetPause(r.json().get("ask_id"))
        r.raise_for_status()
        return r.json()

    async def log(self, lines: list[str]) -> None:
        if lines:
            await self.http.post("/v1/log", json={"lines": lines[:200]})

    async def result(self, patch: str, summary: str, title: str, body: str, documents: dict[str, str] | None = None) -> None:
        r = await self.http.post("/v1/result", json={"patch": patch, "summary": summary, "title": title, "body": body, "documents": documents or {}})
        if r.status_code >= 400:
            raise RuntimeError(f"the gateway refused the result: {r.json().get('detail', r.text)}")

    async def fail(self, message: str) -> None:
        await self.http.post("/v1/fail", json={"message": message[:2000]})
