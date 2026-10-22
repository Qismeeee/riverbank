import json
import os
from pathlib import Path

import httpx
import pytest

from etl import (
    AddlivetagClient,
    AddlivetagExtractor,
    AddlivetagTransformer,
    LocalRawStore,
    Manifest,
    Pipeline,
    PostgresLoader,
    RawRun,
    Segmenter,
    ValidationError,
)
from etl.extract import slug

FIX = Path(__file__).parent / "fixtures"
MARKET = json.loads((FIX / "market_ao_mua.json").read_text(encoding="utf-8"))
MANIFEST = Manifest(
    run_id="addlivetag_2026-10-22",
    source="addlivetag",
    run_date="2026-10-22",
    pulled_at="2026-10-22T01:00:00+00:00",
    raw_prefix="data/raw/addlivetag/2026-10-22",
    market=[
        {
            "archetype": "fashion",
            "keyword": "áo mưa",
            "path": "m/fashion__ao-mua.json",
            "status": "ok",
        }
    ],
    shops=[
        {
            "shop_id": 1218336683,
            "archetype": "fashion",
            "path": "s/1218336683.json",
            "status": "ok",
            "segment": "brand",
            "revenue_30d_src": 3287947224,
        },
        {"shop_id": 984839978, "archetype": "fashion", "path": "s/984839978.json", "status": "ok"},
    ],
)
SHOP_PAYLOADS = {
    1218336683: {
        "products": [
            {"itemId": 25171045245, "name": "trùng với market", "price": 146620, "sales": 12733},
            {
                "itemId": 25171045999,
                "name": "chỉ có ở shop-products",
                "price": 99000,
                "sales": 40,
                "rating": 4.7,
            },
        ]
    },
    984839978: {"products": []},
}
TRANSFORMER = AddlivetagTransformer()


def _run(manifest=MANIFEST, market=None, shops=None) -> RawRun:
    return RawRun(
        manifest,
        market if market is not None else {"m/fashion__ao-mua.json": MARKET},
        shops if shops is not None else SHOP_PAYLOADS,
    )


def _client(handler, **kw) -> AddlivetagClient:
    http = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    return AddlivetagClient("k", http=http, sleep=kw.get("sleep", lambda s: None))


def test_segmenter():
    seg = Segmenter()
    assert seg.classify("Tiệm túi sắc màu", 500_000_000) == "sme"
    assert seg.classify("YODY Official Store", 500_000_000) == "brand"
    assert seg.classify("Sony Store Online", 500_000_000) == "brand"
    assert seg.classify("Goojodoq Offical Shop.VN", 500_000_000) == "brand"
    assert seg.classify("Acer Gadget Partner-Newease", 500_000_000) == "brand"
    assert seg.classify("UNEED STORE VN", 268_000_000) == "sme"
    assert seg.classify("shop to", 5_000_000_000) == "brand"
    assert seg.classify("shop nhỏ", 10_000_000) is None
    assert seg.classify(None, 100_000_000) == "sme"


def test_gift_detection():
    assert TRANSFORMER.is_gift("[QUÀ TẶNG KHÔNG BÁN] Khăn lau")
    assert TRANSFORMER.is_gift("Túi quà (không bán riêng)")
    assert not TRANSFORMER.is_gift("Quà tặng cưới cao cấp set 3 món")


def test_transform_cleans_merges_and_derives():
    snap = TRANSFORMER.transform(_run(), "2026-10-22")
    rep, products = snap.report, snap.products
    ids = {p.item_id for p in products}
    assert 90000000001 not in ids and rep.dropped_gift == 1
    assert 90000000002 not in ids and rep.dropped_price == 1
    assert 77777 not in ids
    assert 25171045999 in ids
    assert rep.merged_duplicates == 1
    fixed = next(p for p in products if p.item_id == 90000000003)
    assert fixed.sold_30d == 10 and rep.fixed_sold_over_total == 1
    s = {x.shop_id: x for x in snap.shops}
    pt = s[984839978]
    assert pt.n_products == 2 and pt.n_tracked == 2
    assert pt.gmv_30d == 99620000 + 1000000 and pt.sold_30d == 1172 + 10
    assert pt.aov == pt.gmv_30d // pt.sold_30d
    assert pt.shop_name == "PTLUXURY " and pt.archetype == "fashion" and pt.segment == "sme"
    d = s[1218336683]
    assert d.n_products == 2 and d.n_tracked == 1 and d.gmv_30d == 0
    assert d.top3_share == 1.0 and d.segment == "brand"
    assert all(p.raw_path for p in products) and all(x.raw_path for x in snap.shops)


def test_transform_reclassifies_brand_from_shop_products_name():
    manifest = Manifest(
        **{
            **MANIFEST.to_dict(),
            "shops": [
                {
                    "shop_id": 37251933,
                    "archetype": "fmcg",
                    "path": "s/x.json",
                    "status": "ok",
                    "segment": "sme",
                }
            ],
        }
    )
    payload = {
        "products": [
            {
                "itemId": 5,
                "name": "Kem",
                "price": 100000,
                "sales": 10,
                "shopName": "L'Oreal Paris Official Store",
            }
        ]
    }
    snap = TRANSFORMER.transform(_run(manifest, {}, {37251933: payload}), "2026-10-22")
    assert (
        snap.shops[0].segment == "brand"
        and snap.shops[0].shop_name == "L'Oreal Paris Official Store"
    )


def test_transform_merges_same_item_and_validates_keys():
    dup = {
        "products": [{"itemId": 1, "price": 10, "sales": 1}, {"itemId": 1, "price": 10, "sales": 1}]
    }
    snap = TRANSFORMER.transform(_run(market={}, shops={984839978: dup}), "2026-10-22")
    assert len(snap.products) == 1 and snap.report.merged_duplicates == 1
    with pytest.raises(ValidationError):
        AddlivetagTransformer._validate([], [snap.products[0], snap.products[0]])


def test_client_rate_limit_and_retry():
    calls, sleeps = [], []

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        assert req.headers["X-API-Key"] == "k"
        if len(calls) == 1:
            return httpx.Response(429, json={"status": "error", "message": "rate"})
        return httpx.Response(200, json={"status": "success", "shops": [{"shopId": 1}]})

    assert _client(handler, sleep=sleeps.append).shop_offer() == [{"shopId": 1}]
    assert len(calls) == 2 and sleeps


def test_client_retries_on_disconnect():
    n = {"calls": 0}

    def handler(req: httpx.Request):
        n["calls"] += 1
        if n["calls"] < 3:
            raise httpx.RemoteProtocolError("Server disconnected")
        return httpx.Response(200, json={"status": "success", "shops": []})

    assert _client(handler).shop_offer() == [] and n["calls"] == 3


def test_client_error_payload_raises():
    c = _client(lambda r: httpx.Response(200, json={"status": "error", "message": "Thiếu API Key"}))
    with pytest.raises(RuntimeError, match="Thiếu API Key"):
        c.market_search("áo mưa")


def _market_or_shop(req: httpx.Request):
    if "market" in req.url.path:
        if req.url.params["q"] == "hỏng":
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=MARKET)
    return httpx.Response(
        200,
        json={
            "status": "success",
            "hasNextPage": False,
            "products": [{"itemId": 1, "price": 5, "sales": 1}],
        },
    )


def test_extractor_writes_raw_and_manifest(tmp_path):
    store = LocalRawStore(tmp_path, "addlivetag/2026-10-22")
    m = AddlivetagExtractor(_client(_market_or_shop), keywords={"fashion": ["áo mưa"]}).extract(
        store, "2026-10-22"
    )
    assert m.run_id == "addlivetag_2026-10-22"
    assert {s["shop_id"]: s["segment"] for s in m.shops} == {1218336683: "brand", 984839978: "sme"}
    assert m.shops[1]["shop_name"] == "PTLUXURY "
    d = tmp_path / "addlivetag" / "2026-10-22"
    assert (d / "manifest.json").exists() and (d / "market" / "fashion__ao-mua.json").exists()
    assert (d / "shop_products" / "984839978.json").exists()
    assert slug("Áo mưa đi xe máy") == "ao-mua-di-xe-may"


def test_extractor_skips_failed_keyword(tmp_path):
    store = LocalRawStore(tmp_path, "addlivetag/2026-10-22")
    m = AddlivetagExtractor(
        _client(_market_or_shop), keywords={"fmcg": ["hỏng", "áo mưa"]}
    ).extract(store, "2026-10-22")
    assert [k["status"] == "ok" for k in m.market] == [False, True]
    assert len(m.shops) == 2


def test_pipeline_reads_back_what_extractor_wrote(tmp_path):
    store = LocalRawStore(tmp_path, "addlivetag/2026-10-22")
    pipeline = Pipeline(
        store,
        AddlivetagExtractor(_client(_market_or_shop), keywords={"fashion": ["áo mưa"]}),
        TRANSFORMER,
    )
    pipeline.extract("2026-10-22")
    run, snap = pipeline.transform("2026-10-22")
    assert run.manifest.run_id == "addlivetag_2026-10-22" and len(run.shops) == 2
    assert snap.report.shops_out == 2 and (tmp_path / "addlivetag/2026-10-22/_report.json").exists()


def test_upsert_sql_shape():
    sql = PostgresLoader.upsert_sql(
        "shop_snapshot", PostgresLoader.SHOP_COLS, ("shop_id", "snapshot_date")
    )
    assert sql.startswith("insert into shop_snapshot (shop_id, snapshot_date")
    assert "on conflict (shop_id, snapshot_date) do update set run_id = excluded.run_id" in sql
    assert "shop_id = excluded.shop_id" not in sql
    assert len(PostgresLoader.PRODUCT_COLS) == 15


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="cần Postgres local: make db-up")
def test_load_replaces_run_and_is_idempotent():
    import psycopg

    snap = TRANSFORMER.transform(_run(), "2026-10-22")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        loader = PostgresLoader(conn)
        loader.apply_schema()
        loader.load(MANIFEST, snap)
        loader.load(MANIFEST, snap)

        def count() -> int:
            sql = "select count(*) from shop_snapshot where run_id = %s"
            return conn.execute(sql, (MANIFEST.run_id,)).fetchone()[0]

        assert count() == 2
        smaller = type(snap)(
            snap.snapshot_date,
            snap.shops[:1],
            [p for p in snap.products if p.shop_id == snap.shops[0].shop_id],
            snap.report,
        )
        loader.load(MANIFEST, smaller)
        assert count() == 1
        loader.load(MANIFEST, snap)
        row = conn.execute(
            "select gmv_30d, segment from v_shop_features where shop_id = 984839978"
        ).fetchone()
        assert row == (100620000, "sme")


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="cần Postgres local: make db-up")
def test_export_writes_csv_per_table(tmp_path):
    import csv

    import psycopg

    from etl.export import CsvExporter

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        loader = PostgresLoader(conn)
        loader.apply_schema()
        loader.load(MANIFEST, TRANSFORMER.transform(_run(), "2026-10-22"))
        written = CsvExporter(conn, tmp_path).export("2026-10-22", ("v_shop_features",))
    rows = list(csv.DictReader(open(written[0], encoding="utf-8")))
    assert {r["shop_id"] for r in rows} >= {"984839978", "1218336683"}
    assert "gmv_30d" in rows[0]
