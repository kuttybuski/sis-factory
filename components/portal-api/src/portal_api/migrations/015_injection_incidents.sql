-- Prompt-injection attempts the factory caught before the text reached an agent. The text itself is not kept: the fingerprint
-- recognises a repeat, the Jira key leads to the original. Each new attempt goes into a security PR on the team's repository;
-- merging that PR is the approval, and from then on the fingerprint is blocked for the team whatever the rules score.
create table injection_incidents (
    id serial primary key,
    team text not null default '',
    repo text not null default '',
    jira_key text not null,
    source text not null,
    verdict text not null check (verdict in ('flag', 'block')),
    score integer not null,
    rules text[] not null default '{}',
    fingerprint text not null,
    actor text not null default '',
    seen integer not null default 1,
    status text not null default 'new' check (status in ('new', 'opening', 'open', 'merged', 'closed', 'failed')),
    pr_number integer,
    pr_url text,
    branch text,
    decided_by text,
    error text,
    created_at timestamptz not null default now(),
    last_seen timestamptz not null default now(),
    attempted_at timestamptz,
    unique (team, fingerprint)
);
create index injection_incidents_status_idx on injection_incidents (status, repo);
