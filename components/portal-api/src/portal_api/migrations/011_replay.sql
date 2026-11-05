-- Merged tickets kept for replay: the ticket, the commit the factory started from, and the diff people finally merged.
create table eval_cases (
    task_id text primary key,
    repo text not null,
    team text,
    base_commit text not null,
    title text not null,
    description text not null,
    reference text not null,
    created_at timestamptz not null default now()
);
create index eval_cases_repo_idx on eval_cases (repo, created_at desc);

-- One replay run of one lesson over a set of cases: requested on the dashboard (or by the cycle), started by orchestrator-worker,
-- finished when EvalWorkflow reports.
create table eval_runs (
    id serial primary key,
    lesson_id integer not null references lessons (id) on delete cascade,
    status text not null default 'requested' check (status in ('requested', 'running', 'done', 'failed')),
    requested_by text not null,
    requested_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz,
    case_ids text[] not null default '{}',
    budget_usd double precision not null,
    pass_score double precision not null,
    result jsonb
);
create index eval_runs_lesson_idx on eval_runs (lesson_id, id desc);
