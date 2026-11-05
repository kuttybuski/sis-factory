alter table teams add column if not exists repos text[] not null default '{}';
