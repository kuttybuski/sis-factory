create table teams (
    id serial primary key,
    name text unique not null,
    jira_projects text[] not null default '{}',
    temporal_namespace text not null default 'default',
    confluence_spaces text[] not null default '{}',
    created_at timestamptz not null default now()
);

create table tasks (
    id text primary key,
    jira_key text not null,
    title text not null default '',
    team text,
    namespace text,
    repo text,
    stage text not null,
    outcome text,
    profile text,
    cost_usd double precision not null default 0,
    budget_usd double precision,
    pr_url text,
    started_at timestamptz not null,
    ended_at timestamptz,
    demo boolean not null default false
);
create index tasks_started_idx on tasks (started_at desc);
create index tasks_team_idx on tasks (team);

create table task_events (
    id bigserial primary key,
    task_id text not null references tasks (id) on delete cascade,
    type text not null,
    ts timestamptz not null,
    payload jsonb not null
);
create index task_events_task_idx on task_events (task_id, id);

create table kill_requests (
    id bigserial primary key,
    task_id text not null references tasks (id) on delete cascade,
    requested_by text not null,
    requested_at timestamptz not null default now()
);
