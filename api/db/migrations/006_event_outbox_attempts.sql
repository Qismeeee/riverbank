alter table event_outbox
    add column attempts integer not null default 0 check (attempts >= 0),
    add column last_error text,
    add column last_attempt_at timestamptz;

create view event_outbox_status as
select
    event_id,
    case_id,
    ordinal,
    payload ->> 'reason' as reason,
    payload ->> 'next_status' as next_status,
    case
        when published_at is not null then 'PUBLISHED'
        when attempts > 0 then 'FAILING'
        else 'PENDING'
    end as status,
    attempts,
    last_error,
    created_at,
    last_attempt_at,
    published_at
from event_outbox;
