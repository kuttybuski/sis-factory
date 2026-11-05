"""Whether the factory may take a team's next ticket. portal-api owns the pauses and the per-team limits and reports both on GET /api/intake;
jira-connector holds tickets back with held_back, and the dashboard and the 3D view show the same reasons."""

from dataclasses import dataclass

ALL = "*"  # a pause for every team


@dataclass(frozen=True)
class Limits:
    max_running: int = 0  # running tasks per team; 0 = only the overall SIF_MAX_CONCURRENT_TASKS
    max_daily: int = 25  # tasks a team may start in 24 hours; 0 = no limit

    @classmethod
    def from_env(cls, env) -> "Limits":
        return cls(max_running=int(env.get("SIF_MAX_CONCURRENT_PER_TEAM", "0")), max_daily=int(env.get("SIF_MAX_TASKS_PER_TEAM_PER_DAY", "25")))


def held_back(team: str, intake: dict | None) -> str | None:
    """Why a team's next ticket must wait: intake is paused (for everyone or this team) or the team is at one of its limits. The ticket keeps
    its label and is picked up once the reason is gone. intake = GET /api/intake; team "" = tickets that belong to no team."""
    if intake is None:
        return None
    paused = {x["team"]: x for x in intake.get("pauses", [])}
    if p := paused.get(ALL) or paused.get(team):
        return f"intake is paused for {'every team' if p['team'] == ALL else team} by {p['paused_by']}" + (f" ({p['reason']})" if p.get("reason") else "")
    limits, n = intake.get("limits") or {}, intake.get("counts", {}).get(team, {})
    who = team or "tickets without a team"
    if (cap := limits.get("max_running", 0)) and n.get("running", 0) >= cap:
        return f"{who} already has {n['running']} running task(s), its limit (SIF_MAX_CONCURRENT_PER_TEAM)"
    if (cap := limits.get("max_daily", 0)) and n.get("started_24h", 0) >= cap:
        return f"{who} started {n['started_24h']} task(s) in 24 hours, its limit (SIF_MAX_TASKS_PER_TEAM_PER_DAY)"
    return None
