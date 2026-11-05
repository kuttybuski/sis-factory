alter table lessons add column eval_status text not null default 'none' check (eval_status in ('none', 'passed', 'failed'));
alter table lessons add column eval_n integer;
alter table lessons add column eval_baseline integer;
alter table lessons add column eval_candidate integer;
