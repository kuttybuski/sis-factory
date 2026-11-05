create table lessons (
    id serial primary key,
    text text not null,
    scope text not null check (scope in ('path', 'repo', 'team', 'global')),
    scope_value text,
    repo text,
    status text not null check (status in ('candidate', 'approved', 'rejected', 'disabled')),
    source text not null,
    author text not null,
    author_role text not null,
    ticket_key text,
    source_url text,
    flags text[] not null default '{}',
    reason text,
    approver text,
    created_at timestamptz not null default now(),
    approved_at timestamptz,
    expires_at timestamptz,
    last_used_at timestamptz,
    use_count integer not null default 0
);
create index lessons_status_idx on lessons (status, created_at desc);
