create table event_outbox (
    event_id uuid primary key references case_events(id),
    case_id uuid not null references cases(case_id),
    ordinal integer not null check (ordinal >= 0),
    payload jsonb not null,
    created_at timestamptz not null default clock_timestamp(),
    published_at timestamptz
);
create index event_outbox_pending on event_outbox (created_at) where published_at is null;
