create extension if not exists vector;

create table context_sources (
    id serial primary key,
    kind text not null check (kind in ('confluence_space', 'github_prs', 'repo_docs', 'qa', 'push')),
    key text not null,  -- space key, owner/repo, or the team name for qa
    team text,
    repo text,
    enabled boolean not null default true,
    config jsonb not null default '{}',
    last_sync_at timestamptz,
    last_status text,
    last_error text,
    stats jsonb not null default '{}',
    unique (kind, key)
);

create table context_docs (
    id bigserial primary key,
    source_id integer not null references context_sources (id) on delete cascade,
    external_id text not null,
    kind text not null,
    title text not null,
    url text not null default '',
    version text not null default '',
    content_hash text not null,
    team text,
    repo text,
    path_hint text[] not null default '{}',
    meta jsonb not null default '{}',
    indexed_at timestamptz not null default now(),
    unique (source_id, external_id)
);

create table context_chunks (
    id bigserial primary key,
    doc_id bigint not null references context_docs (id) on delete cascade,
    ord integer not null,
    heading_path text not null default '',
    text text not null,
    kind text not null default 'text',
    tokens integer not null,
    flagged boolean not null default false,
    embedding vector,  -- dimension depends on the embedding model; chunks are filtered by embedding_model
    embedding_model text,
    tsv tsvector generated always as (to_tsvector('english', coalesce(heading_path, '') || ' ' || text)) stored
);
create index context_chunks_tsv_idx on context_chunks using gin (tsv);
create index context_chunks_doc_idx on context_chunks (doc_id);

create table context_quarantine (
    id bigserial primary key,
    source_id integer not null references context_sources (id) on delete cascade,
    external_id text not null,
    title text not null,
    rules text[] not null,
    excerpt text not null,
    created_at timestamptz not null default now()
);

create table context_usage (
    id bigserial primary key,
    task_id text not null,
    chunk_id bigint,
    doc_id bigint,
    title text not null,
    url text not null default '',
    heading_path text not null default '',
    section text not null,
    rank integer not null,
    score double precision not null,
    created_at timestamptz not null default now()
);
create index context_usage_task_idx on context_usage (task_id);

create table context_gaps (
    id bigserial primary key,
    task_id text,
    query text not null,
    best_score double precision,
    repo text,
    team text,
    created_at timestamptz not null default now()
);
