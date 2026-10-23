create table sellers (
    seller_id text primary key,
    created_at timestamptz not null default current_timestamp
);
create table seller_shops (
    seller_id text not null references sellers(seller_id),
    shop_id text not null,
    platform text not null default 'shopee',
    external_shop_id bigint,
    primary key (seller_id, shop_id),
    unique (platform, shop_id)
);
create table seller_consents (
    consent_id uuid primary key,
    seller_id text not null,
    shop_id text not null,
    recorded_at timestamptz not null,
    payload jsonb not null,
    foreign key (seller_id, shop_id) references seller_shops(seller_id, shop_id),
    check (payload->>'purpose' = 'WORKING_CAPITAL_UNDERWRITING')
);
create index seller_consents_scope on seller_consents(seller_id, shop_id, recorded_at);
create table seller_feature_snapshots (
    snapshot_id uuid primary key,
    seller_id text not null,
    shop_id text not null,
    captured_at timestamptz not null,
    payload jsonb not null,
    payload_hash text not null,
    foreign key (seller_id, shop_id) references seller_shops(seller_id, shop_id),
    check (payload->>'payout_origin' in ('observed', 'simulated')),
    check (payload->>'seller_id' = seller_id and payload->>'shop_id' = shop_id)
);
create index seller_features_scope on seller_feature_snapshots(seller_id, shop_id, captured_at);

create table cases (
    case_id uuid primary key,
    seller_id text not null,
    shop_id text not null,
    status text not null check (status in ('ANALYZING','BLOCKED_INPUT','NO_LOAN_NEEDED',
        'AWAITING_STATEMENT','PENDING_REVIEW','ESCALATED','ACCEPTED','REJECTED')),
    version bigint not null check (version > 0),
    created_at timestamptz not null,
    request_key text not null,
    request_hash text not null,
    header jsonb not null,
    requested_amount bigint generated always as
        ((header->'plan'->>'requested_amount')::bigint) stored not null check (requested_amount > 0),
    check (header->>'seller_id' = seller_id and header->>'shop_id' = shop_id),
    unique (seller_id, request_key)
);
create index cases_queue on cases(status, created_at, case_id);
create index cases_seller on cases(seller_id, created_at);

create table assessment_runs (
    id uuid primary key,
    case_id uuid not null references cases(case_id),
    ordinal integer not null check (ordinal >= 0),
    payload jsonb not null,
    gap bigint generated always as ((payload->'cashflow'->>'gap')::bigint) stored not null
        check (gap >= 0),
    amount_proposed bigint generated always as
        ((payload->'cashflow'->>'amount_proposed')::bigint) stored not null
        check (amount_proposed >= 0),
    unique (case_id, ordinal),
    unique (id, case_id),
    check (payload->>'run_id' = id::text),
    check (jsonb_typeof(payload->'cashflow') = 'object')
);
create table evidence (
    id uuid primary key,
    case_id uuid not null references cases(case_id),
    ordinal integer not null check (ordinal >= 0),
    payload jsonb not null,
    run_id uuid generated always as ((payload->>'run_id')::uuid) stored not null,
    code text generated always as (payload->>'code') stored not null,
    unique (case_id, ordinal),
    unique (run_id, code),
    foreign key (run_id, case_id) references assessment_runs(id, case_id),
    check (payload->>'evidence_id' = id::text and payload->>'case_id' = case_id::text)
);
create table generated_documents (
    id uuid primary key,
    case_id uuid not null references cases(case_id),
    ordinal integer not null check (ordinal >= 0),
    payload jsonb not null,
    run_id uuid generated always as ((payload->>'run_id')::uuid) stored not null,
    attempt integer generated always as ((payload->>'attempt')::integer) stored not null,
    unique (case_id, ordinal),
    unique (run_id, attempt),
    unique (id, run_id, case_id),
    foreign key (run_id, case_id) references assessment_runs(id, case_id),
    check (attempt between 1 and 3),
    check (payload->>'document_id' = id::text)
);
create table decisions (
    case_id uuid primary key references cases(case_id),
    payload jsonb not null,
    run_id uuid generated always as ((payload->>'run_id')::uuid) stored not null,
    document_id uuid generated always as ((payload->>'document_id')::uuid) stored not null,
    decision_type text generated always as (payload->>'decision_type') stored not null,
    final_amount bigint generated always as ((payload->>'final_amount')::bigint) stored,
    final_tenor_weeks integer generated always as
        ((payload->>'final_tenor_weeks')::integer) stored,
    foreign key (document_id, run_id, case_id) references generated_documents(id, run_id, case_id),
    check ((decision_type = 'REJECT' and final_amount is null and final_tenor_weeks is null)
        or (decision_type in ('ACCEPT_PROPOSAL','ACCEPT_COUNTER') and final_amount > 0
            and final_amount is not null and final_tenor_weeks between 2 and 26
            and final_tenor_weeks is not null))
);
create table handoffs (
    case_id uuid primary key references decisions(case_id),
    payload jsonb not null,
    idempotency_key uuid generated always as ((payload->>'idempotency_key')::uuid) stored unique,
    status text generated always as (payload->>'status') stored not null,
    check (status in ('PENDING','SENDING','SUCCEEDED','FAILED'))
);
create table case_events (
    id uuid primary key,
    case_id uuid not null references cases(case_id),
    ordinal integer not null check (ordinal >= 0),
    payload jsonb not null,
    unique (case_id, ordinal),
    check (payload->>'event_id' = id::text)
);

create function underwriting_immutable() returns trigger language plpgsql as $$
begin
    raise exception 'immutable underwriting record';
end;
$$;
create trigger evidence_immutable before update or delete on evidence
    for each row execute function underwriting_immutable();
create trigger runs_immutable before update or delete on assessment_runs
    for each row execute function underwriting_immutable();
create trigger documents_immutable before update or delete on generated_documents
    for each row execute function underwriting_immutable();
create trigger decisions_immutable before update or delete on decisions
    for each row execute function underwriting_immutable();
create trigger events_immutable before update or delete on case_events
    for each row execute function underwriting_immutable();
create trigger features_immutable before update or delete on seller_feature_snapshots
    for each row execute function underwriting_immutable();
create trigger consents_immutable before update or delete on seller_consents
    for each row execute function underwriting_immutable();

create function underwriting_handoff_frozen() returns trigger language plpgsql as $$
begin
    if new.payload->'payload' is distinct from old.payload->'payload'
       or new.payload->>'payload_hash' is distinct from old.payload->>'payload_hash'
       or new.payload->>'idempotency_key' is distinct from old.payload->>'idempotency_key' then
        raise exception 'immutable handoff package';
    end if;
    return new;
end;
$$;
create trigger handoff_package_frozen before update on handoffs
    for each row execute function underwriting_handoff_frozen();

create view v_seller_payout_weekly as
select s.snapshot_id, s.seller_id, s.shop_id, s.captured_at,
       s.payload->>'source' as source, s.payload->>'payout_origin' as payout_origin,
       (w.value->>'week_start')::date as week_start,
       (w.value->>'amount')::bigint as payout_vnd,
       s.payload->>'etl_run_id' as etl_run_id
from seller_feature_snapshots s
cross join lateral jsonb_array_elements(s.payload->'weekly_payouts') w(value);
