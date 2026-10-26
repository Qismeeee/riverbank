create table copilot_turns (
    id uuid primary key,
    seq bigserial unique,
    case_id uuid not null references cases(case_id),
    payload jsonb not null,
    check (payload->>'turn_id' = id::text)
);
create index copilot_turns_case_seq on copilot_turns (case_id, seq);
create trigger copilot_turns_immutable before update or delete on copilot_turns
    for each row execute function underwriting_immutable();
