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
