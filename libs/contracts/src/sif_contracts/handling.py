"""Handling policy: how the factory treats a Jira epic, feature, story or task.

A policy is a few switches plus free-text instructions. The factory sets one per kind; a team (a Temporal namespace) may override any
field, and fields it leaves unset are inherited. Pure functions only (no I/O): portal-api stores and validates, jira-connector snapshots the
resolved policy into the workflow input, and the orchestrator and gateway read it.

The instructions land in the agent's trusted prompt block, so they get the same gates as lessons: size limit, injection and secret scans
(done by portal-api when the text is written).
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

KINDS = ("epic", "feature", "story", "task")
SPLITTABLE = ("epic", "feature")
MAX_CHILDREN = 15

Split = Literal["none", "story", "feature"]


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str = ""  # free text appended to every agent's guidance for this kind
    split_into: Split = "none"  # a planner agent proposes child issues of this kind
    split_approval: bool = False  # a human approves the proposed split on Jira before any child is created
    intent: bool = False  # write intent.md
    intent_approval: bool = False  # a human approves intent.md on Jira
    design: bool = False  # the architect agent writes design.md
    design_approval: bool = False  # a human approves design.md on Jira
    implement: bool = True  # the developer agent opens the draft PR


FIELDS = tuple(Policy.model_fields)

_SPLIT = {"split_into": "story", "split_approval": True, "implement": False}
# story and task match what the factory did before policies existed. Epics and features were never meant to be coded in one go.
BUILTIN: dict[str, dict[str, Any]] = {
    "epic": dict(_SPLIT),
    "feature": dict(_SPLIT),
    "story": {},
    "task": {},
}


def _merge(*layers: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for layer in layers:
        out.update({k: v for k, v in (layer or {}).items() if k in FIELDS})
    return out


def check(kind: str, fields: dict[str, Any], max_chars: int = 4000) -> list[str]:
    """Why a full set of fields cannot be a policy for this kind. Empty when it is fine."""
    if kind not in KINDS:
        return [f"kind must be one of {', '.join(KINDS)}"]
    try:
        p = Policy(**{k: v for k, v in fields.items() if k in FIELDS})
    except ValidationError as e:
        return [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
    errors = []
    if len(p.instructions) > max_chars:
        errors.append(f"instructions can be at most {max_chars} characters")
    if p.split_into != "none":
        if kind not in SPLITTABLE:
            errors.append(f"a {kind} cannot be split; only {' and '.join(SPLITTABLE)} can")
        elif kind == "feature" and p.split_into != "story":
            errors.append("a feature can only be split into stories")
        elif kind == "epic" and p.split_into not in ("story", "feature"):
            errors.append("an epic can be split into stories or features")
        if p.intent or p.design or p.implement:
            errors.append("a kind that is split hands the work to its children: turn off intent, design and implement")
    else:
        if not p.implement:
            errors.append("with no split and no implementation there is nothing for the factory to do")
        if p.split_approval:
            errors.append("split_approval needs split_into")
    if p.intent_approval and not p.intent:
        errors.append("intent_approval needs intent")
    if p.design_approval and not p.design:
        errors.append("design_approval needs design")
    return errors


def resolve(kind: str, factory: dict[str, Any] | None = None, team: dict[str, Any] | None = None) -> tuple[Policy, dict[str, str]]:
    """The policy in force: team over factory over built-in, field by field. Also says where each field came from."""
    base = BUILTIN.get(kind, {})
    merged = _merge(base, factory, team)
    sources = {}
    for f in FIELDS:
        sources[f] = "team" if f in (team or {}) else "factory" if f in (factory or {}) else "builtin"
    return Policy(**merged), sources


def as_dict(p: Policy) -> dict[str, Any]:
    return p.model_dump()


def from_dict(d: dict[str, Any] | None) -> Policy:
    """A policy snapshot carried in a workflow input. Unknown or missing keys fall back to defaults so old snapshots still load."""
    return Policy(**_merge(d))


# ------------------------------------------------------------------------------------ what each agent role is asked to do
OUTPUT_FILE = {"planner": "split.json", "intent": "intent.md", "architect": "design.md"}

ROLE_PROMPTS = {
    "planner": (
        "Your role: planner. Read the parent issue and the repository, then propose how to break the work into child issues. "
        f"Write one file, split.json: a JSON list of at most {MAX_CHILDREN} objects with \"title\" and \"description\". Each description says what "
        "to change and ends with acceptance criteria. Children should be independently deliverable. Do not change any other file and do not write code."),
    "intent": (
        "Your role: write the intent. Read the ticket and the repository, then write one file, intent.md: the problem, who it is for, the outcome "
        "that counts as success, what is out of scope, and open questions. Do not design the solution and do not write code. "
        "Do not change any other file."),
    "architect": (
        "Your role: architect. Read the ticket, the intent (if given) and the repository, then write one file, design.md: the approach, the "
        "components and files affected, interfaces and data changes, risks, and a test plan. Another agent will implement it, so be specific "
        "enough to build from. Do not write production code and do not change any other file."),
    "developer": (
        "Your role: developer. Implement the ticket. If intent.md or design.md are present under docs/sif/<ticket>/, follow them; if you must "
        "deviate, say why in your final summary. Keep those documents unchanged."),
}


def guidance_for(role: str, instructions: str = "") -> str:
    """The role prompt plus the team or factory instructions, as one block for the agent's trusted guidance."""
    parts = [ROLE_PROMPTS.get(role, ROLE_PROMPTS["developer"])]
    if instructions.strip():
        parts.append("How this organisation wants this kind of issue handled:\n" + instructions.strip())
    return "\n\n".join(parts)


def docs_dir(jira_key: str) -> str:
    return f"docs/sif/{jira_key}"


def parse_split(text: str, limit: int = MAX_CHILDREN) -> list[dict[str, str]] | str:
    """The children from a planner's split.json, or the reason it cannot be used."""
    try:
        data = json.loads(text)
    except ValueError:
        return "split.json is not valid JSON"
    if not isinstance(data, list) or not data:
        return "split.json must be a non-empty list"
    if len(data) > limit:
        return f"split.json has {len(data)} children; the limit is {limit}"
    out = []
    for i, item in enumerate(data, 1):
        title = str(item.get("title", "")).strip() if isinstance(item, dict) else ""
        desc = str(item.get("description", "")).strip() if isinstance(item, dict) else ""
        if not title or not desc:
            return f"child {i} needs a title and a description"
        out.append({"title": title[:200], "description": desc[:8000]})
    return out


def kind_of(issue_type: str, type_map: str) -> str:
    """The handling kind for a Jira issue type name, using 'jira name:kind,...' pairs. Anything unmapped is treated as a task."""
    pairs = (p.split(":", 1) for p in type_map.split(",") if ":" in p)
    mapping = {a.strip().lower(): b.strip().lower() for a, b in pairs}
    kind = mapping.get(issue_type.strip().lower(), "task")
    return kind if kind in KINDS else "task"


def jira_type_for(kind: str, type_map: str) -> str:
    """The Jira issue type name to create for a kind: the first name in the map that maps to it, e.g. 'Story'."""
    pairs = (p.split(":", 1) for p in type_map.split(",") if ":" in p)
    name = next((a.strip() for a, b in pairs if b.strip().lower() == kind), kind)
    return name.title()
