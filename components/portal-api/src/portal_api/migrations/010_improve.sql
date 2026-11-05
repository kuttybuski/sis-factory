-- Which lessons each task was given, and which it was deliberately not given (the comparison group), so outcomes can be attributed.
create table lesson_uses (
    task_id text not null,
    lesson_id integer not null references lessons (id) on delete cascade,
    applied boolean not null,
    at timestamptz not null default now(),
    primary key (task_id, lesson_id)
);
create index lesson_uses_lesson_idx on lesson_uses (lesson_id);

-- Commits people added to the PR branch before merging: what the agent did not get right.
alter table tasks add column human_fixups integer;

-- One row per run of the self-improvement cycle: what it found and what it changed, for the Learning page and the audit trail.
create table improvement_runs (
    id serial primary key,
    ran_at timestamptz not null default now(),
    trigger text not null,
    report jsonb not null
);
