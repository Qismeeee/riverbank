import csv
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import httpx
from openpyxl import Workbook

from services.cashflow import CashFlowEngine, CashFlowInput
from services.shopee import (
    ColumnError,
    EscrowDetail,
    OpenApiSource,
    Order,
    ReturnCase,
    SellerExportParser,
    SellerExportSource,
    ShopData,
    ShopeeClient,
    ShopeeCredentials,
    ShopToken,
    WeeklyAggregator,
)
from services.shopee.openapi_client import sign

ROOT = Path(__file__).resolve().parents[2]
CREDS = ShopeeCredentials(partner_id=100001, partner_key="k" * 32, host="https://example.test")
TOKEN = ShopToken(shop_id=7, access_token="t", refresh_token="r", expires_at=0)
PARSER = SellerExportParser()


def _client(handler) -> ShopeeClient:
    http = httpx.Client(base_url=CREDS.host, transport=httpx.MockTransport(handler))
    return ShopeeClient(CREDS, http=http, now=lambda: 1_760_000_000)


def _generate(out: Path, shops: int, weeks: int | None = None) -> None:
    cmd = [
        sys.executable,
        str(ROOT / "data/synth/generate.py"),
        "--out",
        str(out),
        "--shops",
        str(shops),
    ]
    if weeks:
        cmd += ["--weeks", str(weeks)]
    subprocess.run(cmd, check=True)


def test_sign_is_deterministic_hmac():
    s = sign("secret", 1, "/api/v2/shop/get_shop_info", 1700000000, "tok", 7)
    assert len(s) == 64 and s == sign(
        "secret", 1, "/api/v2/shop/get_shop_info", 1700000000, "tok", 7
    )
    assert s != sign("secret", 1, "/api/v2/shop/get_shop_info", 1700000001, "tok", 7)


def test_auth_url_has_sign_and_redirect():
    url = _client(lambda r: httpx.Response(200)).auth_url(
        "https://riverbank.vercel.app/shopee/callback"
    )
    assert "/api/v2/shop/auth_partner?" in url and "sign=" in url and "redirect=" in url


def test_openapi_source_fetches_orders_escrow_returns():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        q = dict(req.url.params)
        assert q["partner_id"] == "100001" and q["shop_id"] == "7" and len(q["sign"]) == 64
        if req.url.path.endswith("get_order_list"):
            return httpx.Response(
                200,
                json={
                    "response": {
                        "order_list": [{"order_sn": "A1"}, {"order_sn": "A2"}],
                        "more": False,
                    }
                },
            )
        if req.url.path.endswith("get_order_detail"):
            assert q["order_sn_list"] == "A1,A2"
            return httpx.Response(
                200,
                json={
                    "response": {
                        "order_list": [
                            {
                                "order_sn": "A1",
                                "create_time": 1_759_000_000,
                                "order_status": "COMPLETED",
                                "total_amount": 150000,
                                "item_list": [{}],
                            },
                            {
                                "order_sn": "A2",
                                "create_time": 1_759_100_000,
                                "order_status": "CANCELLED",
                                "total_amount": 90000,
                            },
                        ]
                    }
                },
            )
        if req.url.path.endswith("get_escrow_detail"):
            return httpx.Response(
                200,
                json={
                    "response": {
                        "order_income": {
                            "escrow_amount": 131250,
                            "buyer_total_amount": 150000,
                            "commission_fee": 6000,
                            "escrow_release_time": 1_759_600_000,
                        }
                    }
                },
            )
        if req.url.path.endswith("get_return_list"):
            return httpx.Response(200, json={"response": {"return": [], "more": False}})
        return httpx.Response(500, json={"error": "unknown"})

    data = OpenApiSource(_client(handler), TOKEN).fetch(1_758_000_000, 1_760_000_000)
    assert [o.order_sn for o in data.orders] == ["A1", "A2"]
    assert data.escrows[0].escrow_amount == 131250 and data.returns == []
    assert calls.count("/api/v2/order/get_order_list") == 2


def test_api_error_is_raised():
    c = _client(
        lambda r: httpx.Response(
            200, json={"error": "error_auth", "message": "Invalid access_token"}
        )
    )
    try:
        c.get_shop_info(TOKEN)
        raise AssertionError("phải raise")
    except RuntimeError as ex:
        assert "error_auth" in str(ex)


def test_weekly_aggregation():
    monday = 1_759_104_000
    data = ShopData(
        orders=[
            Order("A", monday + 3600, "COMPLETED", 100),
            Order("B", monday + 7 * 86400, "COMPLETED", 300),
            Order("C", monday, "CANCELLED", 999),
        ],
        escrows=[
            EscrowDetail("A", 90, 100, escrow_release_time=monday + 7 * 86400),
            EscrowDetail("B", 270, 300, escrow_release_time=monday + 14 * 86400),
        ],
        returns=[ReturnCase("R1", "A", monday + 2 * 86400, "REFUND_PAID", 100)],
    )
    s = WeeklyAggregator(weeks=3, as_of=date(2025, 10, 15)).build(1, data)
    assert s.week_start == ["2025-09-29", "2025-10-06", "2025-10-13"]
    assert s.gmv == [100, 300, 0] and s.orders == [1, 1, 0]
    assert s.payout == [0, 90, 270] and s.returns == [1, 0, 0]
    assert s.return_rate == 0.5


def test_seller_export_source_round_trip(tmp_path):
    _generate(tmp_path, shops=6, weeks=8)
    shop_dir = next((tmp_path / "seller_export").iterdir())
    source = SellerExportSource(
        shop_dir / "don_hang.csv", shop_dir / "doanh_thu.csv", shop_dir / "tra_hang.csv"
    )
    data = source.fetch(0, 2_000_000_000)
    assert data.orders and data.escrows
    assert all(e.escrow_amount < e.buyer_total_amount for e in data.escrows)
    s = WeeklyAggregator(weeks=8, as_of=date(2026, 10, 20)).build(shop_dir.name, data)
    assert sum(s.payout) > 0 and len(s.payout) == 8
    j = json.loads(next((tmp_path / "openapi").iterdir()).read_text(encoding="utf-8"))
    assert {"shop_info", "order_list", "escrow_detail", "return_list"} <= set(j)


def test_missing_column_reports_headers(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("Cột lạ 1,Cột lạ 2,Cột lạ 3\n1,2,3\n", encoding="utf-8")
    try:
        PARSER.orders(p)
        raise AssertionError("phải raise")
    except ColumnError as ex:
        assert "order_sn" in str(ex) and "cột lạ 1" in str(ex)


def test_parser_aliases_are_overridable(tmp_path):
    class MyParser(SellerExportParser):
        ALIASES = {**SellerExportParser.ALIASES, "order_sn": ["ma don"]}

    p = tmp_path / "x.csv"
    header = "ma don,Ngày đặt hàng,Trạng thái đơn hàng,Tổng giá trị đơn hàng (VND)"
    p.write_text(f"{header}\nX1,2026-10-01 10:00,Hoàn thành,150000\n", encoding="utf-8")
    assert MyParser().orders(p)[0].order_sn == "X1"


def test_income_xlsx_multi_sheet_keeps_order_rows_only(tmp_path):
    wb = Workbook()
    wb.active.title = "Summary"
    wb.active.append(["Tên đăng nhập", "shop_x"])
    ws = wb.create_sheet("Income")
    ws.append(
        [
            "Đơn hàng/Sản phẩm",
            "Mã đơn hàng",
            "Ngày đặt hàng",
            "Ngày hoàn thành thanh toán",
            "Tổng tiền đã thanh toán",
            "Phí Cố Định",
            "Phí Dịch vụ",
            "Phí Xử Lý Giao Dịch",
            "Tổng tiền Người mua trả",
        ]
    )
    ws.append(
        [
            "Đơn hàng",
            "2510A1",
            "2026-10-01 10:00",
            "2026-10-08 09:00",
            131250,
            6000,
            6000,
            6750,
            150000,
        ]
    )
    ws.append(
        [
            "Sản phẩm",
            "2510A1",
            "2026-10-01 10:00",
            "2026-10-08 09:00",
            131250,
            6000,
            6000,
            6750,
            150000,
        ]
    )
    p = tmp_path / "bao_cao_doanh_thu.xlsx"
    wb.save(p)
    rows = PARSER.income(p)
    assert len(rows) == 1
    e = rows[0]
    assert e.order_sn == "2510A1" and e.escrow_amount == 131250 and e.seller_transaction_fee == 6750
    assert e.buyer_total_amount == 150000 and e.escrow_release_time


def test_synthetic_scenarios_reproduce_golden_cases(tmp_path):
    _generate(tmp_path, shops=2)
    rows = list(csv.DictReader(open(tmp_path / "shops.csv", encoding="utf-8-sig")))
    got = {}
    for row in rows:
        d = tmp_path / "seller_export" / row["shop_id"]
        data = SellerExportSource(
            d / "don_hang.csv", d / "doanh_thu.csv", d / "tra_hang.csv"
        ).fetch(0, 2_000_000_000)
        s = WeeklyAggregator(weeks=12, as_of=date(2026, 10, 20)).build(row["shop_id"], data)
        got[row["scenario"]] = CashFlowEngine().compute(
            CashFlowInput(
                cash_on_hand=int(row["cash_on_hand"]),
                supplier_schedule=json.loads(row["supplier_schedule"]),
                sell_weeks=6,
                payout_weekly_history=s.payout,
            )
        )
    a, b = got["A_no_loan"], got["B_loan_90m"]
    assert a.gap == 0 and a.payout_warning_threshold == 20_000_000
    assert (b.gap, b.amount_proposed, b.tenor_weeks, b.weekly_deduction) == (
        80_000_000,
        90_000_000,
        8,
        11_250_000,
    )
