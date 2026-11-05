-- What a team writes down about how it works (sif_contracts.knowledge): one row per team and section, every saved version kept.
create table team_knowledge (
    team text not null,
    section text not null,
    content text not null,
    version integer not null,
    flags text[] not null default '{}',
    updated_by text not null,
    updated_at timestamptz not null default now(),
    confirmed_by text,
    confirmed_at timestamptz,
    primary key (team, section)
);

create table team_knowledge_history (
    id bigserial primary key,
    team text not null,
    section text not null,
    content text not null,
    version integer not null,
    updated_by text not null,
    updated_at timestamptz not null default now()
);
create index team_knowledge_history_idx on team_knowledge_history (team, section, version desc);

-- Suggestions someone dismissed, so they are not offered again.
create table team_knowledge_dismissed (
    team text not null,
    section text not null,
    text_hash text not null,
    by text not null,
    at timestamptz not null default now(),
    primary key (team, section, text_hash)
);
