create table jobs (
    id uuid primary key,
    kind text not null,
    case_id uuid not null references cases(case_id),
    submitted_by text not null,
    payload jsonb not null,
    status text not null default 'QUEUED'
        check (status in ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
    attempts integer not null default 0 check (attempts >= 0),
    max_attempts integer not null check (max_attempts > 0),
    lease_until timestamptz,
    content bytea,
    result jsonb,
    error jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (status <> 'RUNNING' or lease_until is not null),
    check (status not in ('SUCCEEDED','FAILED') or content is null)
);
create index jobs_ready on jobs (created_at) where status in ('QUEUED','RUNNING');
create index jobs_case on jobs (case_id, created_at);
