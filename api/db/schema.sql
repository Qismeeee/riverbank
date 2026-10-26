-- Riverbank schema. Chạy: psql "$DATABASE_URL" -f api/db/schema.sql (idempotent).
-- Một DB duy nhất (Postgres). Feature và trạng thái app cùng chỗ, Data Analyst chỉ SELECT trên view.

create table if not exists etl_run (
    run_id        text primary key,            -- addlivetag_2026-10-22
    source        text not null,               -- addlivetag | seller_export | openapi
    pulled_at     timestamptz not null,
    raw_prefix    text not null,               -- s3://bucket/raw/addlivetag/2026-10-22/ hoặc path local
    n_shops       int not null default 0,
    n_products    int not null default 0,
    note          text
);

create table if not exists shop_snapshot (
    shop_id       bigint not null,
    snapshot_date date   not null,
    run_id        text   not null references etl_run(run_id),
    shop_name     text,
    rating        numeric(3,2),
    archetype     text,                        -- fashion | electronics | fmcg | home | other
    segment       text not null default 'sme', -- sme | brand
    n_products    int  not null,
    n_tracked     int  not null,               -- sản phẩm có >= 8 ngày lịch sử (có sold30d)
    gmv_all       bigint not null,             -- Σ revenueAll
    gmv_30d       bigint not null,             -- Σ revenue30d, chỉ sản phẩm tracked
    sold_30d      int    not null,
    aov           int,                         -- gmv_30d / sold_30d
    top3_share    numeric(4,3),                -- tập trung doanh thu: top 3 sản phẩm / gmv_all
    raw_path      text not null,
    primary key (shop_id, snapshot_date)
);

create table if not exists product_snapshot (
    item_id       bigint not null,
    shop_id       bigint not null,
    snapshot_date date   not null,
    run_id        text   not null references etl_run(run_id),
    name          text,
    price         int    not null,
    sales_total   int    not null,
    sold_30d      int,
    revenue_30d   bigint,
    revenue_all   bigint,
    days_tracked  int,
    rating        numeric(3,2),
    cat_id        int,
    commission_rate numeric(5,4),
    raw_path      text not null,
    primary key (item_id, snapshot_date)
);

alter table shop_snapshot add column if not exists segment text not null default 'sme';

create index if not exists product_snapshot_shop on product_snapshot (shop_id, snapshot_date);

-- View chỉ đọc cho Data Analyst agent: shop mới nhất kèm tốc độ bán giữa 2 lần chụp (nếu có).
-- Drop rồi tạo lại vì Postgres không cho đổi thứ tự cột của view đang có.
drop view if exists v_shop_features;
create or replace view v_shop_features as
with latest as (
    select distinct on (shop_id) * from shop_snapshot order by shop_id, snapshot_date desc
),
prev as (
    select distinct on (s.shop_id) s.*
    from shop_snapshot s join latest l on l.shop_id = s.shop_id and s.snapshot_date < l.snapshot_date
    order by s.shop_id, s.snapshot_date desc
),
delta as (
    select p.shop_id,
           sum(cur.sales_total - p.sales_total) as sold_delta,
           sum((cur.sales_total - p.sales_total) * cur.price) as gmv_delta,
           max(cur.snapshot_date) - max(p.snapshot_date) as delta_days
    from product_snapshot p
    join product_snapshot cur on cur.item_id = p.item_id and cur.snapshot_date > p.snapshot_date
    group by p.shop_id
)
select l.shop_id, l.snapshot_date, l.shop_name, l.rating, l.archetype, l.segment, l.n_products, l.n_tracked,
       l.gmv_all, l.gmv_30d, l.sold_30d, l.aov, l.top3_share,
       d.sold_delta, d.gmv_delta, d.delta_days,
       case when d.delta_days > 0 then d.gmv_delta * 7 / d.delta_days end as gmv_weekly_est,
       l.raw_path
from latest l left join delta d on d.shop_id = l.shop_id;

-- ===== Trạng thái app (TDD §4.1). Sơ đồ: api/db/riverbank.dbml =====
-- Không lưu PII của buyer, chỉ số tổng hợp theo shop. Seller dùng mã giả danh, danh tính thật ở ngân hàng.
-- shop_snapshot, product_snapshot, shop_weekly không FK tới shops: ETL nạp cả shop công khai chưa là seller.

create table if not exists sellers (
    seller_id        text primary key,           -- mã giả danh
    bank_cif_ref     text unique,                -- token tham chiếu CIF, không phải CCCD
    entity_type      text not null check (entity_type in ('individual', 'household_business')),
    ekyc_verified_at timestamptz,
    created_at       timestamptz not null default now()
);

create table if not exists shops (
    shop_id         bigint primary key,
    seller_id       text references sellers(seller_id),
    shop_name       text,
    region          text,
    status          text,                        -- normal | banned | frozen
    shop_created_at date,
    source          text not null                -- addlivetag | seller_export | openapi | synth
);

create table if not exists shop_weekly (
    shop_id            bigint not null,
    week_start         date   not null,
    source             text   not null,
    run_id             text references etl_run(run_id),
    gmv                bigint not null,
    orders             int    not null,
    units              int,
    payout             bigint not null,          -- net sau phí sàn, nguồn BR-04
    platform_fees      bigint,
    returns            int    not null default 0,
    refund_amount      bigint not null default 0,
    cancelled_orders   int,
    late_ship_orders   int,
    disputes           int,
    rating_avg         numeric(3,2),
    low_star_reviews   int,                      -- review 1–2 sao
    repeat_buyer_share numeric(4,3),             -- tổng hợp, không lưu buyer_id
    primary key (shop_id, week_start, source)
);

create table if not exists consents (
    consent_id text primary key,
    seller_id  text   not null references sellers(seller_id),
    shop_id    bigint not null references shops(shop_id),
    scopes     text[] not null,                  -- orders, payout, returns, shop_health
    granted_at timestamptz not null,
    expires_at timestamptz,
    revoked_at timestamptz
);

create table if not exists bank_users (
    user_id        text primary key,
    staff_ref      text not null unique,
    display_name   text,
    role           text not null check (role in ('appraiser', 'approver', 'admin')),
    approval_limit bigint,                       -- thẩm quyền phê duyệt, VND
    active         boolean not null default true
);

create table if not exists policy_sets (
    policy_set_id  text primary key,
    version        text  not null,
    source_hash    text  not null,               -- sha256 của policy.md
    params         jsonb not null,               -- BL-01..BL-11
    effective_from date  not null,
    effective_to   date,
    approved_by    text
);

create table if not exists policy_rules (
    policy_set_id text    not null references policy_sets(policy_set_id),
    rule_id       text    not null,              -- R05, R09, ...
    description   text    not null,
    hard          boolean not null,
    params        jsonb,
    primary key (policy_set_id, rule_id)
);

create table if not exists fee_schedules (
    fee_schedule_id   text primary key,
    policy_set_id     text not null references policy_sets(policy_set_id),
    interest_rate_bps int  not null,             -- lãi năm, basis point
    fees              jsonb,                     -- [{name, amount|rate}]
    effective_from    date not null
);

create table if not exists cases (
    case_id       text primary key,
    seller_id     text   not null references sellers(seller_id),
    shop_id       bigint not null references shops(shop_id),
    consent_id    text   not null references consents(consent_id),
    policy_set_id text   not null references policy_sets(policy_set_id),
    status        text   not null default 'DRAFT' check (status in (
        'DRAFT', 'BLOCKED_INPUT', 'ANALYZING', 'NO_LOAN_NEEDED', 'AWAITING_STATEMENT', 'VERIFYING',
        'PENDING_REVIEW', 'ESCALATED', 'ACCEPTED', 'REJECTED', 'HANDOFF_OK', 'HANDOFF_ERROR')),
    consent_ok    boolean not null,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index if not exists cases_status on cases (status);
create index if not exists cases_seller on cases (seller_id);

create table if not exists case_plans (
    case_id           text primary key references cases(case_id),
    lot_value         bigint not null,
    cash_on_hand      bigint not null,
    supplier_schedule jsonb  not null,           -- [{week, amount}]
    sell_weeks        int    not null,
    amount_requested  bigint not null,           -- không dùng làm số đề xuất (BR-08)
    input_hash        text   not null,
    submitted_at      timestamptz not null default now()
);

create table if not exists input_checks (
    id         bigserial primary key,
    case_id    text    not null references cases(case_id),
    check_kind text    not null check (check_kind in ('excess_pii', 'injection', 'sensitive_attr', 'hard_cap')),
    passed     boolean not null,
    detail     text,                             -- đã che, không lưu nội dung vi phạm
    checked_at timestamptz not null default now()
);

create table if not exists evidence (
    evidence_id    text primary key,             -- E1, E5, E6a, ...
    case_id        text    not null references cases(case_id),
    kind           text    not null,
    schema_version text    not null,
    payload_hash   text    not null,
    payload_ref    text    not null,             -- S3 key hoặc bảng + khóa
    summary        jsonb,
    source_run_id  text references etl_run(run_id),
    immutable      boolean not null default true,
    created_at     timestamptz not null default now()
);

create index if not exists evidence_case on evidence (case_id);

create table if not exists cashflow_results (
    id                       bigserial primary key,
    case_id                  text   not null references cases(case_id),
    evidence_id              text   not null unique references evidence(evidence_id),
    purpose                  text   not null check (purpose in ('proposal', 'decision_recalc')),
    amount_input             bigint,             -- số người duyệt nhập khi approve_other
    engine_version           text   not null,
    payout_base              bigint not null,
    payout_stress            bigint not null,
    gap                      bigint not null,
    first_neg_week           int,
    last_neg_week            int,
    amount_proposed          bigint not null,
    tenor_weeks              int    not null,
    weekly_deduction         bigint not null,
    payout_warning_threshold bigint,             -- BR-07
    base_table               jsonb  not null,
    stress_table             jsonb  not null,
    tenor_trace              jsonb,
    created_at               timestamptz not null default now()
);

create table if not exists risk_results (
    id            bigserial primary key,
    case_id       text not null references cases(case_id),
    evidence_id   text not null unique references evidence(evidence_id),
    model_version text not null,
    pd            numeric(5,4) not null,
    factors       jsonb not null,                -- [{name, value, direction, shap}]
    created_at    timestamptz not null default now()
);

create table if not exists cic_results (
    id           bigserial primary key,
    case_id      text not null references cases(case_id),
    evidence_id  text unique references evidence(evidence_id),
    source       text not null check (source in ('mock', 'bank_api')),
    status       text not null check (status in ('ok', 'unavailable')),
    bureau_group int check (bureau_group between 1 and 5),
    outstanding  bigint,
    queried_at   timestamptz not null
);

create table if not exists policy_results (
    id                 bigserial primary key,
    case_id            text   not null references cases(case_id),
    policy_set_id      text   not null references policy_sets(policy_set_id),
    cashflow_result_id bigint not null references cashflow_results(id),
    evidence_id        text   not null unique references evidence(evidence_id),
    all_hard_pass      boolean not null,
    created_at         timestamptz not null default now()
);

create table if not exists policy_rule_results (
    policy_result_id bigint  not null references policy_results(id),
    policy_set_id    text    not null,
    rule_id          text    not null,
    pass             boolean not null,
    hard             boolean not null,
    missing_data     boolean not null default false,
    observed         jsonb,
    threshold        jsonb,
    primary key (policy_result_id, rule_id),
    foreign key (policy_set_id, rule_id) references policy_rules(policy_set_id, rule_id)
);

create table if not exists statement_checks (
    id                       bigserial primary key,
    case_id                  text    not null references cases(case_id),
    required                 boolean not null,   -- số xin > 2,5× mức dữ liệu sàn (BL-05)
    file_ref                 text,
    platform_payout_total    bigint,
    statement_platform_total bigint,
    statement_other_total    bigint,             -- dòng ngoài sàn, case D
    mismatch_pct             numeric(5,2),
    compare_table            jsonb,
    evidence_id              text unique references evidence(evidence_id),
    checked_at               timestamptz
);

create table if not exists documents (
    id              bigserial primary key,
    case_id         text not null references cases(case_id),
    kind            text not null check (kind in ('underwriter_memo', 'seller_explainer')),
    attempt         int  not null,               -- BL-07: lần 3 không đạt thì chuyển người
    status          text not null check (status in ('draft', 'validated', 'failed')),
    body            text not null,
    fee_schedule_id text references fee_schedules(fee_schedule_id),
    created_at      timestamptz not null default now()
);

create table if not exists document_citations (
    document_id bigint not null references documents(id),
    sentence_no int    not null,
    evidence_id text   not null references evidence(evidence_id),
    text        text   not null,
    primary key (document_id, sentence_no, evidence_id)
);

create table if not exists decisions (
    id                     bigserial primary key,
    case_id                text not null references cases(case_id),
    type                   text not null check (type in ('approve_proposed', 'approve_other', 'reject')),
    amount_final           bigint,
    tenor_final            int,
    weekly_deduction_final bigint,
    cashflow_result_id     bigint references cashflow_results(id),
    policy_result_id       bigint references policy_results(id),
    actor_id               text not null references bank_users(user_id),
    reason                 text,
    decided_at             timestamptz not null,
    expires_at             timestamptz           -- decided_at + 14 ngày (BL-10)
);

create table if not exists handoffs (
    id           bigserial primary key,
    case_id      text   not null references cases(case_id),
    decision_id  bigint not null unique references decisions(id),
    status       text   not null check (status in ('pending', 'ok', 'error')),
    los_ref      text,
    payload_json jsonb  not null,
    attempts     int    not null default 0,
    last_error   text,
    sent_at      timestamptz
);

create table if not exists case_events (
    id           bigserial primary key,
    case_id      text   not null references cases(case_id),
    from_status  text,
    to_status    text   not null,
    actor_type   text   not null check (actor_type in ('user', 'system')),
    actor_id     text   not null,                -- user_id hoặc tên bước hệ thống
    evidence_ids text[] not null default '{}',
    note         text,
    at           timestamptz not null default now()
);

create index if not exists case_events_case on case_events (case_id, at);

create table if not exists jobs (
    id         bigserial primary key,
    case_id    text references cases(case_id),
    kind       text not null,                    -- analyze | verify_statement | write_memo | handoff
    status     text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed')),
    run_after  timestamptz not null default now(),
    attempts   int not null default 0,
    locked_at  timestamptz,
    last_error text
);

create index if not exists jobs_pick on jobs (status, run_after);

-- NFR-05: case_events append-only; evidence immutable không sửa, không xóa.
create or replace function rb_forbid_change() returns trigger language plpgsql as $$
begin
    raise exception '% trên % bị cấm', tg_op, tg_table_name;
end $$;

create or replace function rb_evidence_guard() returns trigger language plpgsql as $$
begin
    if old.immutable then
        raise exception '% trên evidence % bị cấm', tg_op, old.evidence_id;
    end if;
    return case when tg_op = 'DELETE' then old else new end;
end $$;

create or replace trigger case_events_append_only
    before update or delete on case_events
    for each row execute function rb_forbid_change();

create or replace trigger evidence_immutable
    before update or delete on evidence
    for each row execute function rb_evidence_guard();
