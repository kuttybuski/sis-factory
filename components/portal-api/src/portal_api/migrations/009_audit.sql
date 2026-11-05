-- Who changed what through the API: every write except the event stream itself. Bodies are not kept (they can hold tokens).
create table audit_log (
    id bigserial primary key,
    at timestamptz not null default now(),
    actor text not null,
    method text not null,
    path text not null,
    status integer not null
);
create index audit_log_at_idx on audit_log (at desc);
