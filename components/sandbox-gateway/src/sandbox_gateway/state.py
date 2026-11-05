"""Per-task state held by the gateway: token, budget, spend, and pending questions. In memory: a gateway restart drops it
(the workflow then times the task out), so run one replica until this moves to Postgres."""

import hashlib
import json
import secrets
from dataclasses import dataclass, field

from injection_guard import make_canary


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class TaskRec:
    task_id: str
    workflow_id: str
    title: str
    description: str
    context_md: str
    repo: str
    commit: str
    team: str | None
    budget_usd: float
    namespace: str = "default"  # the team's Temporal namespace
    guidance: str = ""
    role: str = "developer"  # planner | intent | architect | developer
    documents: dict[str, str] = field(default_factory=dict)  # earlier stages' intent.md / design.md, handed to the agent
    spent_usd: float = 0.0
    estimated: bool = False  # true once any cost came from the fallback price table
    canary: str = field(default_factory=make_canary)
    asks: int = 0
    budget_ask_id: str | None = None
    finished: bool = False


class Registry:
    def __init__(self) -> None:
        self.by_hash: dict[str, TaskRec] = {}
        self.by_id: dict[str, tuple[str, TaskRec]] = {}

    def register(self, rec: TaskRec) -> str:
        if rec.task_id in self.by_id:
            self.remove(rec.task_id)
        token = "sif_" + secrets.token_hex(24)
        self.by_hash[hash_token(token)] = rec
        self.by_id[rec.task_id] = (hash_token(token), rec)
        return token

    def lookup(self, token: str) -> TaskRec | None:
        return self.by_hash.get(hash_token(token))

    def get(self, task_id: str) -> TaskRec | None:
        entry = self.by_id.get(task_id)
        return entry[1] if entry else None

    def remove(self, task_id: str) -> None:
        if entry := self.by_id.pop(task_id, None):
            self.by_hash.pop(entry[0], None)


def load_prices(raw: str | None) -> dict[str, tuple[float, float]]:
    """USD per million tokens (input, output) by model; '*' is the fallback. Costs from the fallback are marked as estimates."""
    table = {"*": (1.0, 5.0)}
    if raw:
        table |= {k: (float(v[0]), float(v[1])) for k, v in json.loads(raw).items()}
    return table


def cost_of(model: str, tokens_in: int, tokens_out: int, prices: dict[str, tuple[float, float]]) -> tuple[float, bool]:
    exact = model in prices
    pin, pout = prices.get(model) or prices["*"]
    return (tokens_in * pin + tokens_out * pout) / 1_000_000, not exact
