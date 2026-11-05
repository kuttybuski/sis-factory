-- The daily knowledge PR: one per repository per day at most, bundling the candidate lessons for that repository into its CLAUDE.md.
create table digests (
    id serial primary key,
    repo text not null,
    team text,
    status text not null default 'opening' check (status in ('opening', 'open', 'merged', 'closed', 'failed')),
    lesson_ids integer[] not null,
    pr_number integer,
    pr_url text,
    branch text,
    merged_by text,
    error text,
    created_at timestamptz not null default now(),
    decided_at timestamptz
);
create index digests_repo_idx on digests (repo, created_at desc);

alter table lessons add column digest_id integer references digests (id) on delete set null;
alter table lessons add column in_repo boolean not null default false;  -- adopted into the repository's CLAUDE.md: no longer sent in the rules block
