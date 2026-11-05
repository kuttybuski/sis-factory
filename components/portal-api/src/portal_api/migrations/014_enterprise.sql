-- Who may act for a team (emails or IdP groups): its knowledge, lessons and tasks. Empty = the global operators, as before.
alter table teams add column if not exists leads text[] not null default '{}';

-- Stopping intake: team '*' stops every team. Running tasks are not touched (Kill does that); new tickets wait until resumed.
create table intake_pauses (
    team text primary key,
    reason text not null default '',
    paused_by text not null,
    paused_at timestamptz not null default now()
);

-- The audit log is append-only: rows cannot be changed, and deleted only by the retention job, which says so for its own transaction.
create or replace function audit_log_append_only() returns trigger language plpgsql as $$
begin
    if tg_op = 'DELETE' and coalesce(current_setting('sif.audit_purge', true), '') = 'on' then
        return old;
    end if;
    raise exception 'audit_log is append-only';
end $$;
create trigger audit_log_append_only before update or delete on audit_log for each row execute function audit_log_append_only();
