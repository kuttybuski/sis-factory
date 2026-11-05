-- A task now follows its PR to merge: when it opened, how many review rounds the agent made, and when a person merged it.
alter table tasks add column pr_opened_at timestamptz;
alter table tasks add column review_rounds integer not null default 0;
alter table tasks add column merged_at timestamptz;
update tasks set pr_opened_at = ended_at where pr_url is not null and pr_opened_at is null;
