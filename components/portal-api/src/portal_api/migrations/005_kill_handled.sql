alter table kill_requests add column handled_at timestamptz;
create index kill_requests_pending_idx on kill_requests (id) where handled_at is null;
