"""Postgres access for portal-api. Raw SQL; every dynamic fragment comes from a fixed allowlist."""

from typing import Any

from psycopg import errors
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from sif_context import db as shared
from sif_contracts.events import EventType, FactoryEvent
from sif_contracts.namespaces import namespace_for

from portal_api.projector import project, to_dt

COLUMNS = ["id", "jira_key", "title", "team", "namespace", "repo", "stage", "outcome", "profile",
           "cost_usd", "budget_usd", "pr_url", "started_at", "ended_at", "demo", "pr_opened_at", "review_rounds", "merged_at", "human_fixups"]

GROUPS = {
    "team": "coalesce(team, '(none)')",
    "project": "split_part(jira_key, '-', 1)",
    "day": "to_char(date_trunc('day', started_at), 'YYYY-MM-DD')",
}


MIGRATIONS = "portal_api.migrations"
make_pool = shared.make_pool


async def open_pool(url: str) -> AsyncConnectionPool:
    return await shared.open_pool(url, MIGRATIONS)


async def migrate(pool: AsyncConnectionPool) -> None:
    await shared.migrate(pool, MIGRATIONS)


async def _team_defaults(conn: Any, jira_key: str) -> dict[str, Any]:
    project_key = jira_key.split("-")[0]
    cur = await conn.execute(
        "select name, temporal_namespace from teams where %s = any(jira_projects) order by id limit 1", (project_key,))
    row = await cur.fetchone()
    return {"team": row["name"], "namespace": row["temporal_namespace"]} if row else {}


async def ingest(pool: AsyncConnectionPool, ev: FactoryEvent, *, demo: bool = False) -> bool:
    """Applies one event: updates the task row and appends to the event log. False if it changed nothing."""
    try:
        async with pool.connection() as conn, conn.transaction():
            row = await (await conn.execute("select * from tasks where id = %s for update", (ev.task_id,))).fetchone()
            defaults = {}
            if row is None and ev.type is EventType.TASK_DISCOVERED and not ev.team:
                defaults = await _team_defaults(conn, ev.jira_key or ev.task_id)
            new = project(row, ev, defaults)
            if new is None:
                return False
            new["demo"] = row["demo"] if row else demo
            if row is None:
                cols = ", ".join(COLUMNS)
                marks = ", ".join(["%s"] * len(COLUMNS))
                await conn.execute(f"insert into tasks ({cols}) values ({marks})", [new[c] for c in COLUMNS])
            else:
                sets = ", ".join(f"{c} = %s" for c in COLUMNS if c != "id")
                await conn.execute(f"update tasks set {sets} where id = %s", [new[c] for c in COLUMNS if c != "id"] + [ev.task_id])
            await conn.execute(
                "insert into task_events (task_id, type, ts, payload) values (%s, %s, %s, %s)",
                (ev.task_id, ev.type.value, to_dt(ev.ts), Jsonb(ev.model_dump(by_alias=True, exclude_none=True, mode="json"))))
            return True
    except errors.UniqueViolation:
        return False  # two concurrent task.discovered events for the same task


def _task_filter(*, team: str | None, outcome: str | None, q: str | None, days: int | None, include_demo: bool) -> tuple[str, list[Any]]:
    where, args = [], []
    if not include_demo:
        where.append("not demo")
    if team:
        where.append("team = %s"); args.append(team)
    if outcome == "active":
        where.append("outcome is null")
    elif outcome:
        where.append("outcome = %s"); args.append(outcome)
    if q:
        where.append("(jira_key ilike %s or title ilike %s)"); args += [f"%{q}%", f"%{q}%"]
    if days:
        where.append("started_at >= now() - make_interval(days => %s)"); args.append(days)
    return (" where " + " and ".join(where)) if where else "", args


async def list_tasks(pool: AsyncConnectionPool, *, team: str | None = None, outcome: str | None = None, q: str | None = None,
                     days: int | None = None, limit: int = 50, offset: int = 0, include_demo: bool = False) -> dict[str, Any]:
    clause, args = _task_filter(team=team, outcome=outcome, q=q, days=days, include_demo=include_demo)
    async with pool.connection() as conn:
        total = (await (await conn.execute(f"select count(*) as n from tasks{clause}", args)).fetchone())["n"]
        rows = await (await conn.execute(
            f"select * from tasks{clause} order by started_at desc limit %s offset %s", [*args, limit, offset])).fetchall()
    return {"items": rows, "total": total}


async def get_task(pool: AsyncConnectionPool, task_id: str) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        task = await (await conn.execute("select * from tasks where id = %s", (task_id,))).fetchone()
        if task is None:
            return None
        events = await (await conn.execute(
            "select id, type, ts, payload from task_events where task_id = %s order by id limit 2000", (task_id,))).fetchall()
    return {"task": task, "events": events}


def _scope(team: str | None, include_demo: bool, alias: str = "") -> tuple[str, list[Any]]:
    """The 'and ...' filters every dashboard view shares: hide demo rows, and keep one team when the header picker names one."""
    sql, args = "" if include_demo else f" and not {alias}demo", []
    if team:
        sql += f" and {alias}team = %s"; args.append(team)
    return sql, args


async def costs(pool: AsyncConnectionPool, *, group_by: str, days: int, include_demo: bool, team: str | None = None) -> list[dict[str, Any]]:
    expr = GROUPS[group_by]  # KeyError for anything outside the allowlist; the API validates first
    scope, args = _scope(team, include_demo)
    order = "key" if group_by == "day" else "cost desc"
    async with pool.connection() as conn:
        return await (await conn.execute(
            f"select {expr} as key, count(*)::int as tasks, coalesce(sum(cost_usd), 0) as cost, "
            f"count(*) filter (where pr_url is not null)::int as prs, count(*) filter (where outcome = 'merged')::int as merged from tasks "
            f"where started_at >= now() - make_interval(days => %s){scope} group by 1 order by {order}", [days, *args])).fetchall()


async def overview(pool: AsyncConnectionPool, *, include_demo: bool, team: str | None = None) -> dict[str, Any]:
    scope, args = _scope(team, include_demo)
    async with pool.connection() as conn:
        stages = await (await conn.execute(
            f"select stage, count(*)::int as n from tasks where outcome is null{scope} group by stage", args)).fetchall()
        # The headline numbers for "humans only review": how much of the work reaches main, and what it costs to get there.
        row = await (await conn.execute(
            f"select coalesce(sum(cost_usd) filter (where started_at >= now() - interval '24 hours'), 0) as spend_24h, "
            f"count(*) filter (where pr_opened_at >= now() - interval '7 days')::int as prs_7d, "
            f"count(*) filter (where outcome is not null and ended_at >= now() - interval '7 days')::int as finished_7d, "
            f"count(*) filter (where outcome = 'pr_opened')::int as in_review, "
            f"count(*) filter (where outcome = 'merged' and merged_at >= now() - interval '30 days')::int as merged_30d, "
            f"count(*) filter (where outcome = 'closed' and ended_at >= now() - interval '30 days')::int as closed_30d, "
            f"coalesce(avg(review_rounds) filter (where outcome = 'merged' and merged_at >= now() - interval '30 days'), 0)::float as rounds_per_merge, "
            f"coalesce(sum(cost_usd) filter (where started_at >= now() - interval '30 days'), 0) as spend_30d "
            f"from tasks where true{scope}", args)).fetchone()
        active = await (await conn.execute(
            f"select * from tasks where outcome is null{scope} order by started_at desc limit 50", args)).fetchall()
    return {"stages": {r["stage"]: r["n"] for r in stages}, "active": active, **row}


async def replay(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    """Events a freshly connected live view needs: every non-demo task that is active, in review, or finished in the last 10 minutes.
    Log lines of in-review tasks are left out: they can be long, and the dashboard's task page has them all."""
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "select e.payload from task_events e join tasks t on t.id = e.task_id "
            "where not t.demo and (t.ended_at is null or t.ended_at > now() - interval '10 minutes' "
            "or (t.outcome = 'pr_opened' and e.type <> 'log.line')) order by e.id")).fetchall()
    return [r["payload"] for r in rows]


async def list_teams(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute("select * from teams order by name")).fetchall()


async def upsert_team(pool: AsyncConnectionPool, *, name: str, jira_projects: list[str], confluence_spaces: list[str], repos: list[str] | None = None,
                      leads: list[str] | None = None) -> dict[str, Any]:
    temporal_namespace = namespace_for(name)  # the team is its namespace
    async with pool.connection() as conn:
        return await (await conn.execute(
            "insert into teams (name, jira_projects, temporal_namespace, confluence_spaces, repos, leads) values (%s, %s, %s, %s, %s, %s) "
            "on conflict (name) do update set jira_projects = excluded.jira_projects, temporal_namespace = excluded.temporal_namespace, "
            "confluence_spaces = excluded.confluence_spaces, repos = excluded.repos, leads = excluded.leads returning *",
            (name, jira_projects, temporal_namespace, confluence_spaces, repos or [], leads or []))).fetchone()


async def active_tasks(pool: AsyncConnectionPool, team: str) -> int:
    async with pool.connection() as conn:
        row = await (await conn.execute("select count(*) as n from tasks where team = %s and ended_at is null and not demo", (team,))).fetchone()
    return row["n"]


async def delete_team(pool: AsyncConnectionPool, name: str) -> bool:
    """Removes the team with its handling overrides, connections and team knowledge. Its task history stays, and so does the history of
    its team knowledge, so what people wrote can be recovered."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("delete from handling_policies where scope = %s", (f"team:{name}",))
        await conn.execute("delete from connections where team = %s", (name,))
        await conn.execute("delete from team_knowledge where team = %s", (name,))
        await conn.execute("delete from team_knowledge_dismissed where team = %s", (name,))
        return (await conn.execute("delete from teams where name = %s", (name,))).rowcount > 0


# ---------------------------------------------------------------- handling policies
async def list_handling(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute("select scope, kind, fields, updated_by, updated_at from handling_policies order by scope, kind")).fetchall()


async def put_handling(pool: AsyncConnectionPool, *, scope: str, kind: str, fields: dict[str, Any], by: str) -> dict[str, Any]:
    async with pool.connection() as conn:
        return await (await conn.execute(
            "insert into handling_policies (scope, kind, fields, updated_by) values (%s, %s, %s, %s) "
            "on conflict (scope, kind) do update set fields = excluded.fields, updated_by = excluded.updated_by, updated_at = now() "
            "returning scope, kind, fields, updated_by, updated_at", (scope, kind, Jsonb(fields), by))).fetchone()


async def delete_handling(pool: AsyncConnectionPool, *, scope: str, kind: str) -> bool:
    async with pool.connection() as conn:
        return (await conn.execute("delete from handling_policies where scope = %s and kind = %s", (scope, kind))).rowcount > 0


# ---------------------------------------------------------------- connections (the secret column is only read by get_connection_secret)
PUBLIC_CONNECTION = "team, provider, config, secret_hint, secret is not null as has_secret, status, detail, checked_at, updated_by, updated_at"


async def list_connections(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute(f"select {PUBLIC_CONNECTION} from connections order by team, provider")).fetchall()


async def get_connection(pool: AsyncConnectionPool, team: str, provider: str) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        return await (await conn.execute(f"select {PUBLIC_CONNECTION} from connections where team = %s and provider = %s", (team, provider))).fetchone()


async def get_connection_secret(pool: AsyncConnectionPool, team: str, provider: str) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        return await (await conn.execute("select config, secret from connections where team = %s and provider = %s and secret is not null", (team, provider))).fetchone()


async def put_connection(pool: AsyncConnectionPool, *, team: str, provider: str, config: dict[str, Any], secret: bytes | None, hint: str | None, by: str) -> dict[str, Any]:
    """Saves a connection. A None secret keeps the stored one. Status goes back to unknown until the next check."""
    async with pool.connection() as conn:
        return await (await conn.execute(
            "insert into connections (team, provider, config, secret, secret_hint, updated_by) values (%s, %s, %s, %s, %s, %s) "
            "on conflict (team, provider) do update set config = excluded.config, secret = coalesce(excluded.secret, connections.secret), "
            "secret_hint = coalesce(excluded.secret_hint, connections.secret_hint), status = 'unknown', detail = null, checked_at = null, "
            f"updated_by = excluded.updated_by, updated_at = now() returning {PUBLIC_CONNECTION}",
            (team, provider, Jsonb(config), secret, hint, by))).fetchone()


async def set_connection_status(pool: AsyncConnectionPool, team: str, provider: str, status: str, detail: str) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        return await (await conn.execute(
            f"update connections set status = %s, detail = %s, checked_at = now() where team = %s and provider = %s returning {PUBLIC_CONNECTION}",
            (status, detail[:500], team, provider))).fetchone()


async def delete_connection(pool: AsyncConnectionPool, team: str, provider: str) -> bool:
    async with pool.connection() as conn:
        return (await conn.execute("delete from connections where team = %s and provider = %s", (team, provider))).rowcount > 0


async def request_kill(pool: AsyncConnectionPool, task_id: str, requested_by: str) -> str:
    """Records a kill request for the orchestrator to act on. Returns 'unknown', 'finished' or 'requested'."""
    async with pool.connection() as conn:
        task = await (await conn.execute("select outcome from tasks where id = %s", (task_id,))).fetchone()
        if task is None:
            return "unknown"
        if task["outcome"] is not None:
            return "finished"
        await conn.execute("insert into kill_requests (task_id, requested_by) values (%s, %s)", (task_id, requested_by))
        return "requested"


async def clear_demo(pool: AsyncConnectionPool) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("delete from tasks where demo")
        return cur.rowcount


# ---------------------------------------------------------------- lessons
_LESSON_STATUSES = ("candidate", "approved", "rejected", "disabled")


async def find_duplicate_lesson(pool: AsyncConnectionPool, *, text: str, scope: str, scope_value: str | None, repo: str | None) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        return await (await conn.execute(
            "select * from lessons where lower(text) = lower(%s) and scope = %s and coalesce(scope_value, '') = coalesce(%s, '') "
            "and coalesce(repo, '') = coalesce(%s, '') and status in ('candidate', 'approved') limit 1",
            (text, scope, scope_value, repo))).fetchone()


async def create_lesson(pool: AsyncConnectionPool, *, text: str, scope: str, scope_value: str | None, repo: str | None, status: str,
                        source: str, author: str, author_role: str, ticket_key: str | None, source_url: str | None,
                        flags: list[str], reason: str | None, ttl_days: int) -> dict[str, Any]:
    approved = status == "approved"
    async with pool.connection() as conn:
        return await (await conn.execute(
            "insert into lessons (text, scope, scope_value, repo, status, source, author, author_role, ticket_key, source_url, flags, reason, "
            "approver, approved_at, expires_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "case when %s then now() end, now() + make_interval(days => %s)) returning *",
            (text, scope, scope_value, repo, status, source, author, author_role, ticket_key, source_url, flags, reason,
             author if approved else None, approved, ttl_days))).fetchone()


async def list_lessons(pool: AsyncConnectionPool, *, status: str | None, limit: int = 200) -> list[dict[str, Any]]:
    where, args = ("where status = %s", [status]) if status else ("", [])
    async with pool.connection() as conn:
        return await (await conn.execute(
            f"select * from lessons {where} order by created_at desc limit %s", [*args, limit])).fetchall()


async def transition_lesson(pool: AsyncConnectionPool, lesson_id: int, *, to: str, allowed_from: tuple[str, ...], by: str, reason: str | None = None) -> dict[str, Any] | None:
    """Moves a lesson to a new status if it is currently in one of allowed_from. None if it does not exist or cannot move."""
    assert to in _LESSON_STATUSES
    async with pool.connection() as conn:
        return await (await conn.execute(
            "update lessons set status = %s, reason = coalesce(%s, reason), "
            "approver = case when %s = 'approved' then %s else approver end, approved_at = case when %s = 'approved' then now() else approved_at end "
            "where id = %s and status = any(%s) returning *",
            (to, reason, to, by, to, lesson_id, list(allowed_from)))).fetchone()


async def approved_lessons(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute("select * from lessons where status = 'approved' and not in_repo and (expires_at is null or expires_at > now())")).fetchall()


async def record_lesson_use(pool: AsyncConnectionPool, ids: list[int]) -> None:
    if ids:
        async with pool.connection() as conn:
            await conn.execute("update lessons set use_count = use_count + 1, last_used_at = now() where id = any(%s)", (ids,))


async def record_lesson_uses(pool: AsyncConnectionPool, task_id: str, applied: list[int], held: list[int]) -> None:
    """The first decision for a task stands: later packs for the same task (review rounds) make the same one anyway."""
    rows = [(task_id, i, True) for i in applied] + [(task_id, i, False) for i in held]
    if rows:
        async with pool.connection() as conn:
            await conn.cursor().executemany("insert into lesson_uses (task_id, lesson_id, applied) values (%s, %s, %s) on conflict do nothing", rows)


async def lesson_outcomes(pool: AsyncConnectionPool, *, days: int) -> list[dict[str, Any]]:
    """Every finished task that was in a lesson's treatment or comparison group, with how it ended."""
    async with pool.connection() as conn:
        return await (await conn.execute(
            "select u.lesson_id, u.task_id, u.applied, t.outcome, t.review_rounds, t.human_fixups from lesson_uses u join tasks t on t.id = u.task_id "
            "where not t.demo and t.outcome is not null and t.outcome <> 'pr_opened' and u.at >= now() - make_interval(days => %s)", (days,))).fetchall()


async def learning_signals(pool: AsyncConnectionPool, *, days: int, team: str | None = None) -> list[dict[str, Any]]:
    """What happened after the factory's own work: review rounds, CI failures, clarifying questions, bounces and merges, with the task's repo
    and team. The raw material of the improvement cycle."""
    scope, args = _scope(team, False, "t.")
    async with pool.connection() as conn:
        return await (await conn.execute(
            "select t.id as task_id, t.repo, t.team, t.outcome, e.type, e.ts, e.payload from task_events e join tasks t on t.id = e.task_id "
            "where e.type in ('review.received', 'pr.merged', 'pr.closed', 'agent.asked_question', 'jira.bounced') "
            f"and e.ts >= now() - make_interval(days => %s){scope} order by e.id limit 20000", [days, *args])).fetchall()


async def finished_tasks(pool: AsyncConnectionPool, *, days: int, team: str | None = None) -> list[dict[str, Any]]:
    scope, args = _scope(team, False)
    async with pool.connection() as conn:
        return await (await conn.execute(
            "select id, repo, team, outcome, review_rounds, human_fixups, cost_usd from tasks where outcome is not null "
            f"and ended_at >= now() - make_interval(days => %s){scope}", [days, *args])).fetchall()


async def record_improvement_run(pool: AsyncConnectionPool, *, trigger: str, report: dict[str, Any]) -> dict[str, Any]:
    async with pool.connection() as conn:
        return await (await conn.execute("insert into improvement_runs (trigger, report) values (%s, %s) returning *", (trigger, Jsonb(report)))).fetchone()


async def list_improvement_runs(pool: AsyncConnectionPool, *, limit: int = 20) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute("select * from improvement_runs order by id desc limit %s", (limit,))).fetchall()


async def put_eval_case(pool: AsyncConnectionPool, *, task_id: str, repo: str, team: str | None, base_commit: str, title: str, description: str, reference: str) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "insert into eval_cases (task_id, repo, team, base_commit, title, description, reference) values (%s,%s,%s,%s,%s,%s,%s) "
            "on conflict (task_id) do update set base_commit = excluded.base_commit, title = excluded.title, description = excluded.description, "
            "reference = excluded.reference, created_at = now()", (task_id, repo, team, base_commit, title, description, reference))


def _case_scope(lesson: dict[str, Any]) -> tuple[str, list[Any]]:
    """The cases a lesson could have affected: its repository, its team, or any for a global lesson."""
    if lesson["scope"] in ("repo", "path"):
        return "where repo = %s", [lesson["repo"] or lesson["scope_value"]]
    if lesson["scope"] == "team":
        return "where team = %s", [lesson["scope_value"]]
    return "", []


async def eval_cases_for(pool: AsyncConnectionPool, lesson: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    where, args = _case_scope(lesson)
    async with pool.connection() as conn:
        return await (await conn.execute(f"select task_id, repo, team, base_commit, title, description, reference from eval_cases {where} "
                                         "order by created_at desc limit %s", [*args, limit])).fetchall()


async def eval_case_counts(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute("select repo, team, count(*)::int as cases, max(created_at) as latest from eval_cases group by 1, 2 order by 3 desc")).fetchall()


async def request_eval_run(pool: AsyncConnectionPool, *, lesson_id: int, by: str, case_ids: list[str], budget_usd: float, pass_score: float) -> dict[str, Any] | None:
    """None if a run for this lesson is already waiting or running."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("select pg_advisory_xact_lock(%s)", (lesson_id,))
        busy = await (await conn.execute("select 1 from eval_runs where lesson_id = %s and status in ('requested', 'running')", (lesson_id,))).fetchone()
        if busy:
            return None
        return await (await conn.execute(
            "insert into eval_runs (lesson_id, requested_by, case_ids, budget_usd, pass_score) values (%s,%s,%s,%s,%s) returning *",
            (lesson_id, by, case_ids, budget_usd, pass_score))).fetchone()


async def claim_eval_runs(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    """Marks requested runs as running and returns them with their cases, so each run is started exactly once."""
    async with pool.connection() as conn, conn.transaction():
        runs = await (await conn.execute(
            "update eval_runs set status = 'running', started_at = now() where id in (select id from eval_runs where status = 'requested' "
            "order by id limit 5 for update skip locked) returning *")).fetchall()
        for r in runs:
            r["cases"] = await (await conn.execute(
                "select task_id, repo, team, base_commit, title, description, reference from eval_cases where task_id = any(%s)", (r["case_ids"],))).fetchall()
    return runs


async def finish_eval_run(pool: AsyncConnectionPool, run_id: int, *, ok: bool, result: dict[str, Any]) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        return await (await conn.execute(
            "update eval_runs set status = %s, finished_at = now(), result = %s where id = %s and status in ('requested', 'running') returning *",
            ("done" if ok else "failed", Jsonb(result), run_id))).fetchone()


async def list_eval_runs(pool: AsyncConnectionPool, *, lesson_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    where, args = ("where r.lesson_id = %s", [lesson_id]) if lesson_id else ("", [])
    async with pool.connection() as conn:
        return await (await conn.execute(
            "select r.id, r.lesson_id, l.text as lesson_text, r.status, r.requested_by, r.requested_at, r.started_at, r.finished_at, "
            f"cardinality(r.case_ids) as cases, r.budget_usd, r.result from eval_runs r join lessons l on l.id = r.lesson_id {where} order by r.id desc limit %s",
            [*args, limit])).fetchall()


async def claim_digests(pool: AsyncConnectionPool, *, every_hours: float) -> list[dict[str, Any]]:
    """Repositories due a knowledge PR: candidate lessons waiting, no knowledge PR open, none in the last every_hours. Each gets a digest row,
    and its lessons are reserved for it, so two workers never open two PRs."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("select pg_advisory_xact_lock(4242)")
        due = await (await conn.execute(
            "select l.repo, array_agg(l.id order by l.id) as ids from lessons l where l.status = 'candidate' and l.scope in ('repo', 'path') "
            "and l.repo is not null and l.digest_id is null and l.flags = '{}' and not exists (select 1 from digests d where d.repo = l.repo "
            "and (d.status in ('opening', 'open') or d.created_at > now() - make_interval(secs => %s))) group by l.repo", (every_hours * 3600,))).fetchall()
        out = []
        for d in due:
            team = await (await conn.execute("select name from teams where %s = any(repos) order by id limit 1", (d["repo"],))).fetchone()
            row = await (await conn.execute("insert into digests (repo, team, lesson_ids) values (%s, %s, %s) returning *",
                                            (d["repo"], team["name"] if team else None, d["ids"]))).fetchone()
            await conn.execute("update lessons set digest_id = %s where id = any(%s)", (row["id"], d["ids"]))
            row["lessons"] = await (await conn.execute(
                "select id, text, source, source_url, author, eval_status, eval_n, eval_baseline, eval_candidate from lessons where id = any(%s) order by id",
                (d["ids"],))).fetchall()
            out.append(row)
    return out


async def digest_opened(pool: AsyncConnectionPool, digest_id: int, *, pr_number: int, pr_url: str, branch: str) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        return await (await conn.execute("update digests set status = 'open', pr_number = %s, pr_url = %s, branch = %s where id = %s and status = 'opening' returning *",
                                         (pr_number, pr_url, branch, digest_id))).fetchone()


async def decide_digest(pool: AsyncConnectionPool, digest_id: int, *, state: str, merged_by: str | None, kept: list[int], error: str | None) -> dict[str, Any] | None:
    """Merged: lessons still in the block are adopted (approved, in the repo), lines people deleted are rejected, and lessons adopted earlier
    whose line is gone are disabled. Closed: the batch is rejected. Failed: the lessons go back in the queue."""
    async with pool.connection() as conn, conn.transaction():
        d = await (await conn.execute("update digests set status = %s, merged_by = %s, error = %s, decided_at = now() where id = %s "
                                      "and status in ('opening', 'open') returning *", (state, merged_by, error, digest_id))).fetchone()
        if d is None:
            return None
        ids, who = d["lesson_ids"], merged_by or "knowledge PR"
        if state == "merged":
            await conn.execute("update lessons set status = 'approved', in_repo = true, approver = %s, approved_at = now() "
                               "where id = any(%s) and id = any(%s) and status = 'candidate'", (who, ids, kept))
            await conn.execute("update lessons set status = 'rejected', reason = 'removed from the knowledge PR before it was merged' "
                               "where id = any(%s) and not (id = any(%s)) and status = 'candidate'", (ids, kept))
            await conn.execute("update lessons set status = 'disabled', in_repo = false, reason = 'its line was removed from the repository''s CLAUDE.md' "
                               "where repo = %s and in_repo and status = 'approved' and not (id = any(%s))", (d["repo"], kept))
        elif state == "closed":
            await conn.execute("update lessons set status = 'rejected', reason = 'the knowledge PR was closed without merging' where id = any(%s) and status = 'candidate'", (ids,))
        else:
            await conn.execute("update lessons set digest_id = null where id = any(%s) and status = 'candidate'", (ids,))
        return d


async def list_digests(pool: AsyncConnectionPool, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    where, args = ("where status = %s", [status]) if status else ("", [])
    async with pool.connection() as conn:
        return await (await conn.execute(f"select * from digests {where} order by id desc limit %s", [*args, limit])).fetchall()


async def team_knowledge(pool: AsyncConnectionPool, team: str) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute("select * from team_knowledge where team = %s", (team,))).fetchall()


async def save_team_knowledge(pool: AsyncConnectionPool, *, team: str, section: str, content: str, flags: list[str], by: str,
                              expected_version: int) -> dict[str, Any] | None:
    """Saves a new version if the section is still at expected_version (0 = not written yet); None if someone saved in between."""
    async with pool.connection() as conn, conn.transaction():
        cur = await (await conn.execute("select version from team_knowledge where team = %s and section = %s for update", (team, section))).fetchone()
        if (cur["version"] if cur else 0) != expected_version:
            return None
        row = await (await conn.execute(
            "insert into team_knowledge (team, section, content, version, flags, updated_by, confirmed_by, confirmed_at) values (%s,%s,%s,%s,%s,%s,%s,now()) "
            "on conflict (team, section) do update set content = excluded.content, version = excluded.version, flags = excluded.flags, "
            "updated_by = excluded.updated_by, updated_at = now(), confirmed_by = excluded.confirmed_by, confirmed_at = now() returning *",
            (team, section, content, expected_version + 1, flags, by, by))).fetchone()
        await conn.execute("insert into team_knowledge_history (team, section, content, version, updated_by) values (%s,%s,%s,%s,%s)",
                           (team, section, content, expected_version + 1, by))
        return row


async def confirm_team_knowledge(pool: AsyncConnectionPool, *, team: str, section: str, by: str) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        return await (await conn.execute("update team_knowledge set confirmed_by = %s, confirmed_at = now() where team = %s and section = %s returning *",
                                         (by, team, section))).fetchone()


async def team_knowledge_history(pool: AsyncConnectionPool, *, team: str, section: str, limit: int = 20) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute("select * from team_knowledge_history where team = %s and section = %s order by version desc limit %s",
                                         (team, section, limit))).fetchall()


async def dismiss_suggestion(pool: AsyncConnectionPool, *, team: str, section: str, text_hash: str, by: str) -> None:
    async with pool.connection() as conn:
        await conn.execute("insert into team_knowledge_dismissed (team, section, text_hash, by) values (%s,%s,%s,%s) on conflict do nothing", (team, section, text_hash, by))


async def dismissed(pool: AsyncConnectionPool, team: str) -> set[tuple[str, str]]:
    async with pool.connection() as conn:
        return {(r["section"], r["text_hash"]) for r in await (await conn.execute("select section, text_hash from team_knowledge_dismissed where team = %s", (team,))).fetchall()}


async def knowledge_sources(pool: AsyncConnectionPool, team: str, repos: list[str]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """What the context index holds for drafting a team's knowledge: its pages and docs, and each repository's README. Empty when the
    context tables are not there (portal-api running without context-ingest)."""
    async with pool.connection() as conn:
        exists = await (await conn.execute("select to_regclass('context_chunks') is not null as ok")).fetchone()
        if not exists["ok"]:
            return [], {}
        chunks = await (await conn.execute(
            "select c.text, d.title, d.url from context_chunks c join context_docs d on d.id = c.doc_id where (d.team = %s or d.repo = any(%s)) "
            "and d.kind in ('page', 'doc') and not c.flagged order by d.indexed_at desc, c.ord limit 400", (team, repos))).fetchall()
        readmes = await (await conn.execute(
            "select distinct on (d.repo) d.repo, c.text from context_docs d join context_chunks c on c.doc_id = d.id where d.repo = any(%s) "
            "and d.kind = 'doc' and lower(d.title) like 'readme%%' and not c.flagged order by d.repo, c.ord", (repos,))).fetchall()
    return chunks, {r["repo"]: r["text"] for r in readmes}


async def get_lesson(pool: AsyncConnectionPool, lesson_id: int) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        return await (await conn.execute("select * from lessons where id = %s", (lesson_id,))).fetchone()


async def set_lesson_eval(pool: AsyncConnectionPool, lesson_id: int, *, status: str, n: int, baseline: int, candidate: int) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        return await (await conn.execute(
            "update lessons set eval_status = %s, eval_n = %s, eval_baseline = %s, eval_candidate = %s where id = %s returning *",
            (status, n, baseline, candidate, lesson_id))).fetchone()


async def finished_task_costs(pool: AsyncConnectionPool, *, group: str, days: int, include_demo: bool, team: str | None = None) -> list[dict[str, Any]]:
    expr = {"team": "coalesce(team, '(none)')", "repo": "coalesce(repo, '(none)')", "profile": "coalesce(profile, '(unrouted)')"}[group]
    scope, args = _scope(team, include_demo)
    async with pool.connection() as conn:
        return await (await conn.execute(
            f"select {expr} as \"group\", cost_usd, outcome from tasks where outcome is not null and ended_at >= now() - make_interval(days => %s){scope}", [days, *args])).fetchall()


async def outcomes_and_errors(pool: AsyncConnectionPool, *, days: int, include_demo: bool, team: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    scope, args = _scope(team, include_demo, "t.")
    async with pool.connection() as conn:
        tasks = await (await conn.execute(f"select t.outcome from tasks t where t.outcome is not null and t.ended_at >= now() - make_interval(days => %s){scope}", [days, *args])).fetchall()
        lines = await (await conn.execute(
            f"select e.payload->>'text' as text from task_events e join tasks t on t.id = e.task_id where t.outcome in ('failed', 'killed', 'timed_out') "
            f"and t.ended_at >= now() - make_interval(days => %s) and e.type = 'log.line' and e.payload->>'source' = 'claude'{scope} limit 2000", [days, *args])).fetchall()
    return tasks, [l["text"] or "" for l in lines]


async def pending_kills(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute("select k.id, k.task_id, k.requested_by, coalesce(t.namespace, 'default') as namespace from kill_requests k left join tasks t on t.id = k.task_id "
            "where k.handled_at is null order by k.id limit 100")).fetchall()


async def mark_kills_handled(pool: AsyncConnectionPool, ids: list[int]) -> int:
    async with pool.connection() as conn:
        return (await conn.execute("update kill_requests set handled_at = now() where id = any(%s) and handled_at is null", (ids,))).rowcount


async def record_audit(pool: AsyncConnectionPool, *, actor: str, method: str, path: str, status: int) -> None:
    async with pool.connection() as conn:
        await conn.execute("insert into audit_log (actor, method, path, status) values (%s, %s, %s, %s)", (actor, method, path, status))


# ---------------------------------------------------------------- intake pauses and limits
async def list_pauses(pool: AsyncConnectionPool) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        return await (await conn.execute("select * from intake_pauses order by team")).fetchall()


async def pause_intake(pool: AsyncConnectionPool, *, team: str, reason: str, by: str) -> dict[str, Any]:
    async with pool.connection() as conn:
        return await (await conn.execute(
            "insert into intake_pauses (team, reason, paused_by) values (%s,%s,%s) on conflict (team) do update set reason = excluded.reason, "
            "paused_by = excluded.paused_by, paused_at = now() returning *", (team, reason, by))).fetchone()


async def resume_intake(pool: AsyncConnectionPool, team: str) -> bool:
    async with pool.connection() as conn:
        return (await conn.execute("delete from intake_pauses where team = %s", (team,))).rowcount > 0


async def intake_counts(pool: AsyncConnectionPool) -> dict[str, dict[str, int]]:
    """Per team ('' = no team): tasks running now and tasks started in the last 24 hours. Demo tasks do not count."""
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            "select coalesce(team, '') as team, count(*) filter (where ended_at is null)::int as running, "
            "count(*) filter (where started_at > now() - interval '24 hours')::int as started_24h from tasks where not demo "
            "and (ended_at is null or started_at > now() - interval '24 hours') group by 1")).fetchall()
    return {r["team"]: {"running": r["running"], "started_24h": r["started_24h"]} for r in rows}


# ---------------------------------------------------------------- retention
async def purge(pool: AsyncConnectionPool, *, event_days: int, audit_days: int) -> dict[str, int]:
    """Deletes what is older than the retention settings: event payloads (they hold ticket text, agent output and code) of finished tasks,
    and audit rows. 0 keeps them forever. The tasks themselves stay, so the numbers on the dashboard do not change."""
    out = {"events": 0, "audit": 0}
    async with pool.connection() as conn, conn.transaction():
        if event_days > 0:
            out["events"] = (await conn.execute(
                "delete from task_events e using tasks t where t.id = e.task_id and t.ended_at is not null and e.ts < now() - make_interval(days => %s)",
                (event_days,))).rowcount
        if audit_days > 0:
            await conn.execute("set local sif.audit_purge = 'on'")
            out["audit"] = (await conn.execute("delete from audit_log where at < now() - make_interval(days => %s)", (audit_days,))).rowcount
    return out


async def list_audit(pool: AsyncConnectionPool, *, limit: int, actor: str | None = None) -> list[dict[str, Any]]:
    where, args = ("where actor = %s", [actor]) if actor else ("", [])
    async with pool.connection() as conn:
        return await (await conn.execute(f"select * from audit_log {where} order by id desc limit %s", [*args, limit])).fetchall()


async def scorecard(pool: AsyncConnectionPool, *, days: int, include_demo: bool, team: str | None = None) -> list[dict[str, Any]]:
    """The pilot numbers per repository: of the tickets the factory took, how many reached main, how often reviewers sent the work back,
    how long it took, and what each merged change cost."""
    scope, args = _scope(team, include_demo)
    async with pool.connection() as conn:
        return await (await conn.execute(
            "select coalesce(repo, '(none)') as repo, count(*)::int as tickets, "
            "count(*) filter (where pr_url is not null)::int as prs, count(*) filter (where outcome = 'merged')::int as merged, "
            "count(*) filter (where outcome = 'closed')::int as closed, count(*) filter (where outcome = 'pr_opened')::int as in_review, "
            "count(*) filter (where outcome in ('failed', 'timed_out', 'bounced', 'killed'))::int as not_delivered, "
            "coalesce(avg(review_rounds) filter (where outcome = 'merged'), 0)::float as rounds_per_merge, "
            "percentile_cont(0.5) within group (order by extract(epoch from pr_opened_at - started_at) / 3600) filter (where pr_opened_at is not null) as hours_to_pr, "
            "percentile_cont(0.5) within group (order by extract(epoch from merged_at - pr_opened_at) / 3600) filter (where merged_at is not null) as hours_to_merge, "
            "coalesce(sum(cost_usd), 0) as cost "
            f"from tasks where started_at >= now() - make_interval(days => %s){scope} group by 1 order by tickets desc", [days, *args])).fetchall()


# ---------------------------------------------------------------- prompt-injection incidents
async def record_incident(pool: AsyncConnectionPool, *, team: str, repo: str, jira_key: str, source: str, verdict: str, score: int, rules: list[str],
                          fingerprint: str, actor: str) -> dict[str, Any]:
    """One row per attempt per team: a repeat only counts up (and a block outranks a flag). `new` says whether this is the first time."""
    async with pool.connection() as conn:
        return await (await conn.execute(
            "insert into injection_incidents (team, repo, jira_key, source, verdict, score, rules, fingerprint, actor) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "on conflict (team, fingerprint) do update set seen = injection_incidents.seen + 1, last_seen = now(), "
            "score = greatest(injection_incidents.score, excluded.score), "
            "verdict = case when 'block' in (injection_incidents.verdict, excluded.verdict) then 'block' else 'flag' end "
            "returning *, (xmax = 0) as new", (team, repo, jira_key, source, verdict, score, rules, fingerprint, actor))).fetchone()


async def claim_incidents(pool: AsyncConnectionPool, *, retry_hours: float = 1) -> list[dict[str, Any]]:
    """Repositories with attempts not yet in a security PR, and no security PR open there: each gets one PR for all of them, so a burst
    of attempts is one PR, not many. A failed attempt to open one is retried after retry_hours."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("select pg_advisory_xact_lock(4243)")
        rows = await (await conn.execute(
            "update injection_incidents i set status = 'opening', attempted_at = now(), error = null where i.repo <> '' "
            "and (i.status = 'new' or (i.status = 'failed' and i.attempted_at < now() - make_interval(secs => %s))) "
            "and not exists (select 1 from injection_incidents o where o.repo = i.repo and o.status in ('opening', 'open')) "
            "returning i.id, i.team, i.repo, i.jira_key, i.source, i.verdict, i.score, i.rules, i.fingerprint, i.actor, i.seen, i.created_at",
            (retry_hours * 3600,))).fetchall()
    groups: dict[str, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: r["id"]):
        groups.setdefault(r["repo"], {"repo": r["repo"], "team": r["team"] or None, "incidents": []})["incidents"].append(r)
    return list(groups.values())


async def incidents_opened(pool: AsyncConnectionPool, ids: list[int], *, pr_number: int, pr_url: str, branch: str) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("update injection_incidents set status = 'open', pr_number = %s, pr_url = %s, branch = %s where id = any(%s) and status = 'opening'",
                                 (pr_number, pr_url, branch, ids))
        return cur.rowcount


async def decide_incidents(pool: AsyncConnectionPool, ids: list[int], *, state: str, by: str | None, error: str | None) -> int:
    """Merged: the fingerprints are blocked for the team from now on. Closed: people judged them harmless. Failed: tried again later."""
    async with pool.connection() as conn:
        cur = await conn.execute("update injection_incidents set status = %s, decided_by = %s, error = %s where id = any(%s) and status in ('opening', 'open')",
                                 (state, by, error, ids))
        return cur.rowcount


async def list_incidents(pool: AsyncConnectionPool, *, team: str | None = None, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    where, args = [], []
    for col, val in (("team", team), ("status", status)):
        if val is not None:
            where.append(f"{col} = %s")
            args.append(val)
    async with pool.connection() as conn:
        return await (await conn.execute(f"select * from injection_incidents {'where ' + ' and '.join(where) if where else ''} order by last_seen desc limit %s",
                                         [*args, limit])).fetchall()


async def team_blocklist(pool: AsyncConnectionPool, team: str) -> list[str]:
    async with pool.connection() as conn:
        rows = await (await conn.execute("select fingerprint from injection_incidents where team = %s and status = 'merged'", (team,))).fetchall()
    return [r["fingerprint"] for r in rows]
