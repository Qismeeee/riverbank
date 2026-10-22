import argparse
from datetime import timedelta
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import config
from services.underwriting.evidence import digest
from services.underwriting.service import utc_now
from services.underwriting.sources import demo_source


def seed(conn, seller_id: str | None = None, shop_id: int | None = None) -> None:
    now = utc_now()
    records = demo_source(now).records
    if shop_id is not None:
        with conn.cursor(row_factory=dict_row) as cursor:
            row = cursor.execute(
                "select shop_id,snapshot_date,run_id,gmv_30d,sold_30d,n_tracked,raw_path,"
                "archetype,segment from shop_snapshot where shop_id=%s "
                "order by snapshot_date desc limit 1",
                (shop_id,),
            ).fetchone()
        if not row or not row["n_tracked"] or row["gmv_30d"] <= 0:
            raise ValueError("ETL shop có tracked GMV dương là bắt buộc")
        consent, feature = records[("demo-b", "demo-b")]
        consent["reference"] = f"demo-consent-{seller_id}-{shop_id}"
        # Estimated weekly payout: 30-day GMV / 30 * 7 * 85%, integer VND.
        payout = row["gmv_30d"] * 7 * 85 // 3000
        for week in feature["weekly_payouts"]:
            week["amount"] = payout
        feature.update(
            seller_id=seller_id,
            shop_id=str(shop_id),
            source="addlivetag",
            disclosure=(
                "shop từ ETL công khai; payout, tuổi shop, mức hỗ trợ và tỷ lệ hoàn mô phỏng"
            ),
            platform_supported_amount=payout * 2,
            etl_run_id=row["run_id"],
            raw_refs=[row["raw_path"]],
            source_snapshot={**row, "snapshot_date": row["snapshot_date"].isoformat()},
            simulation={
                "version": "gmv30d-flat-85pct-v1",
                "gmv_30d": row["gmv_30d"],
                "weekly_formula": "gmv_30d * 7 * 85 // 3000",
                "anchor_date": now.date().isoformat(),
            },
        )
        records = {(seller_id, str(shop_id)): (consent, feature)}
    with conn.transaction():
        for (seller, shop), (consent, features) in records.items():
            conn.execute(
                "insert into sellers (seller_id) values (%s) on conflict do nothing", (seller,)
            )
            conn.execute(
                "insert into seller_shops (seller_id,shop_id,external_shop_id) values (%s,%s,%s) "
                "on conflict (seller_id,shop_id) do nothing",
                (seller, shop, shop_id),
            )
            consent["expires_at"] = (now + timedelta(days=30)).isoformat()
            conn.execute(
                "insert into seller_consents (consent_id,seller_id,shop_id,recorded_at,payload) "
                "values (%s,%s,%s,%s,%s)",
                (str(uuid4()), seller, shop, now, Jsonb(consent)),
            )
            conn.execute(
                "insert into seller_feature_snapshots "
                "(snapshot_id,seller_id,shop_id,captured_at,payload,payload_hash) "
                "values (%s,%s,%s,%s,%s,%s)",
                (str(uuid4()), seller, shop, now, Jsonb(features), digest(features)),
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed explicit DEMO underwriting inputs")
    parser.add_argument("--etl-shop-id", type=int)
    parser.add_argument("--seller-id")
    parser.add_argument("--simulate-payout", action="store_true")
    args = parser.parse_args()
    if config.ENV != "local" or config.WORKFLOW_MODE != "demo":
        raise SystemExit("Seed chỉ chạy local với RIVERBANK_WORKFLOW_MODE=demo")
    if args.etl_shop_id is not None and (not args.seller_id or not args.simulate_payout):
        parser.error("ETL seed cần --seller-id và --simulate-payout")
    if not config.database_url():
        parser.error("DATABASE_URL is required")
    with psycopg.connect(config.database_url()) as conn:
        seed(conn, args.seller_id, args.etl_shop_id)
    print("Demo features and demo consent appended; CIC/risk/LOS remain mock")


if __name__ == "__main__":
    main()
