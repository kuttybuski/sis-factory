-- A team's own connection to Jira or GitHub. config holds the non-secret parts; secret is encrypted with SIF_SECRET_KEY and never leaves portal-api
-- except to the services that use it (internal token). A team with no row uses the shared credentials from those services' environment.
create table connections (
    team text not null,
    provider text not null check (provider in ('jira', 'github')),
    config jsonb not null default '{}',
    secret bytea,
    secret_hint text,
    status text not null default 'unknown' check (status in ('unknown', 'connected', 'failed')),
    detail text,
    checked_at timestamptz,
    updated_by text not null default 'dashboard',
    updated_at timestamptz not null default now(),
    primary key (team, provider)
);
