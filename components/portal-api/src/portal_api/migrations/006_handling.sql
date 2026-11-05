-- How epics, features, stories and tasks are handled. scope is 'factory' or 'team:<name>'; a team row holds only the fields it overrides.
create table handling_policies (
    scope text not null,
    kind text not null check (kind in ('epic', 'feature', 'story', 'task')),
    fields jsonb not null default '{}',
    updated_by text not null default 'dashboard',
    updated_at timestamptz not null default now(),
    primary key (scope, kind)
);
