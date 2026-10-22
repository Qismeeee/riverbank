from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FEES = {"commission": 0.04, "service": 0.04, "transaction": 0.045}
ESCROW_LAG_DAYS = (5, 9)
RETURN_LAG_DAYS = (2, 10)

ARCHETYPES = {
    "fashion": dict(aov=(180_000, 0.6), orders_per_week=(90, 0.35), return_rate=0.09),
    "electronics": dict(aov=(650_000, 0.7), orders_per_week=(35, 0.4), return_rate=0.05),
    "fmcg": dict(aov=(120_000, 0.4), orders_per_week=(160, 0.3), return_rate=0.02),
    "home": dict(aov=(320_000, 0.5), orders_per_week=(55, 0.35), return_rate=0.06),
}

SCENARIOS = {
    "A_no_loan": dict(
        archetype="fmcg",
        payout_target=40_000_000,
        cash_on_hand=100_000_000,
        supplier_schedule=[
            {"week": 0, "amount": 60_000_000},
            {"week": 2, "amount": 80_000_000},
        ],
    ),
    "B_loan_90m": dict(
        archetype="fashion",
        payout_target=70_000_000,
        cash_on_hand=120_000_000,
        supplier_schedule=[{"week": 0, "amount": 200_000_000}],
    ),
    "D_offplatform": dict(
        archetype="home",
        payout_target=20_000_000,
        cash_on_hand=30_000_000,
        supplier_schedule=[{"week": 0, "amount": 150_000_000}],
    ),
    "E_young_shop": dict(
        archetype="electronics",
        payout_target=30_000_000,
        cash_on_hand=20_000_000,
        supplier_schedule=[{"week": 0, "amount": 90_000_000}],
        shop_age_months=2,
    ),
    "L_payout_drop": dict(
        archetype="fashion",
        payout_target=70_000_000,
        cash_on_hand=120_000_000,
        supplier_schedule=[{"week": 0, "amount": 200_000_000}],
        shock=dict(week=-3, payout=0.6, returns=3.0),
    ),
}

VN_HEADERS = {
    "orders": [
        "Mã đơn hàng",
        "Ngày đặt hàng",
        "Trạng thái đơn hàng",
        "Tổng giá trị đơn hàng (VND)",
        "Người mua",
    ],
    "income": [
        "Mã đơn hàng",
        "Ngày đặt hàng",
        "Ngày hoàn thành thanh toán",
        "Tổng giá trị đơn hàng (VND)",
        "Phí cố định",
        "Phí dịch vụ",
        "Phí thanh toán",
        "Tổng tiền đã thanh toán",
    ],
    "returns": [
        "Mã yêu cầu trả hàng",
        "Mã đơn hàng",
        "Ngày tạo",
        "Trạng thái",
        "Số tiền hoàn",
    ],
}


@dataclass
class Shop:
    shop_id: int
    shop_name: str
    archetype: str
    scenario: str
    shop_created: str
    cash_on_hand: int
    supplier_schedule: str
    rating_star: float


def lognormal(rng: random.Random, mean: float, sigma: float) -> float:
    mu = math.log(mean) - sigma * sigma / 2
    return rng.lognormvariate(mu, sigma)


def season(d: date) -> float:
    f = 1.0
    if d.month == d.day and d.month >= 9:
        f *= 2.2
    if d.day >= 27 or d.day <= 2:
        f *= 1.15
    if d.month == 2 and d.day <= 10:
        f *= 0.5
    return f


def vn(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def gen_shop(
    rng: random.Random, i: int, scenario: str, start: date, weeks: int
) -> tuple[Shop, list, list, list]:
    sc = SCENARIOS.get(scenario, {})
    arch = sc.get("archetype") or rng.choice(list(ARCHETYPES))
    a = ARCHETYPES[arch]
    age_m = sc.get("shop_age_months", rng.randint(7, 48))
    if "payout_target" in sc:
        net = a["aov"][0] * (1 - sum(FEES.values())) * 0.96
        base_orders = sc["payout_target"] / net
    else:
        base_orders = a["orders_per_week"][0] * rng.uniform(0.5, 1.6)
    orders, income, returns = [], [], []
    weekly_payout = []
    sn = 0
    for w in range(weeks):
        wk = start + timedelta(weeks=w)
        shock = sc.get("shock")
        shock_hit = shock and w >= weeks + shock["week"]
        n = max(
            0,
            int(rng.gauss(base_orders * season(wk), base_orders * a["orders_per_week"][1])),
        )
        if shock_hit:
            n = int(n * shock["payout"])
        rr = a["return_rate"] * (shock["returns"] if shock_hit else 1.0)
        pay_w = 0
        for _ in range(n):
            sn += 1
            order_sn = f"{wk.strftime('%y%m%d')}{i:03d}{sn:05d}"
            t = datetime.combine(
                wk + timedelta(days=rng.randint(0, 6)),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            t += timedelta(hours=rng.randint(7, 23), minutes=rng.randint(0, 59))
            ts = int(t.timestamp())
            amount = int(lognormal(rng, a["aov"][0], a["aov"][1]) // 1000 * 1000)
            cancelled = rng.random() < 0.04
            status = "CANCELLED" if cancelled else "COMPLETED"
            orders.append(
                dict(
                    order_sn=order_sn,
                    create_time=ts,
                    order_status=status,
                    total_amount=amount,
                    buyer=f"user{rng.randint(1000, 99999)}",
                )
            )
            if cancelled:
                continue
            release = ts + rng.randint(*ESCROW_LAG_DAYS) * 86400
            fee_c = int(amount * FEES["commission"])
            fee_s = int(amount * FEES["service"])
            fee_t = int(amount * FEES["transaction"])
            escrow = amount - fee_c - fee_s - fee_t
            income.append(
                dict(
                    order_sn=order_sn,
                    create_time=ts,
                    escrow_release_time=release,
                    buyer_total_amount=amount,
                    commission_fee=fee_c,
                    service_fee=fee_s,
                    seller_transaction_fee=fee_t,
                    escrow_amount=escrow,
                )
            )
            pay_w += escrow
            if rng.random() < rr:
                returns.append(
                    dict(
                        return_sn=f"R{order_sn}",
                        order_sn=order_sn,
                        create_time=ts + rng.randint(*RETURN_LAG_DAYS) * 86400,
                        status=rng.choice(["REFUND_PAID", "ACCEPTED", "CLOSED"]),
                        refund_amount=amount,
                    )
                )
        weekly_payout.append(pay_w)
    median_pay = sorted(weekly_payout)[len(weekly_payout) // 2] if weekly_payout else 0
    cash = sc.get("cash_on_hand", int(median_pay * rng.uniform(0.4, 1.8) // 1_000_000 * 1_000_000))
    schedule = sc.get(
        "supplier_schedule",
        [
            {
                "week": 0,
                "amount": int(median_pay * rng.uniform(2, 4) // 1_000_000 * 1_000_000),
            }
        ],
    )
    shop = Shop(
        shop_id=100000 + i,
        shop_name=f"{arch}_shop_{i:03d}",
        archetype=arch,
        scenario=scenario,
        shop_created=(start - timedelta(days=30 * age_m)).isoformat(),
        cash_on_hand=cash,
        supplier_schedule=json.dumps(schedule),
        rating_star=round(rng.uniform(4.3, 4.95), 2),
    )
    return shop, orders, income, returns


def write_csv(path: Path, header: list[str], rows: list[list]):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synth/out")
    ap.add_argument("--shops", type=int, default=50)
    ap.add_argument("--weeks", type=int, default=26)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--end", default="2026-10-20", help="thứ hai của tuần cuối")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    out = Path(args.out)
    (out / "seller_export").mkdir(parents=True, exist_ok=True)
    (out / "openapi").mkdir(parents=True, exist_ok=True)
    end = date.fromisoformat(args.end)
    start = end - timedelta(weeks=args.weeks - 1)

    scenarios = list(SCENARIOS) + ["normal"] * (args.shops - len(SCENARIOS))
    shops = []
    for i, scenario in enumerate(scenarios[: args.shops]):
        shop, orders, income, returns = gen_shop(rng, i, scenario, start, args.weeks)
        shops.append(shop)
        d = out / "seller_export" / str(shop.shop_id)
        d.mkdir(exist_ok=True)
        write_csv(
            d / "don_hang.csv",
            VN_HEADERS["orders"],
            [
                [
                    o["order_sn"],
                    vn(o["create_time"]),
                    "Hoàn thành" if o["order_status"] == "COMPLETED" else "Đã hủy",
                    o["total_amount"],
                    o["buyer"],
                ]
                for o in orders
            ],
        )
        write_csv(
            d / "doanh_thu.csv",
            VN_HEADERS["income"],
            [
                [
                    x["order_sn"],
                    vn(x["create_time"]),
                    vn(x["escrow_release_time"]),
                    x["buyer_total_amount"],
                    x["commission_fee"],
                    x["service_fee"],
                    x["seller_transaction_fee"],
                    x["escrow_amount"],
                ]
                for x in income
            ],
        )
        write_csv(
            d / "tra_hang.csv",
            VN_HEADERS["returns"],
            [
                [
                    r["return_sn"],
                    r["order_sn"],
                    vn(r["create_time"]),
                    r["status"],
                    r["refund_amount"],
                ]
                for r in returns
            ],
        )
        with open(out / "openapi" / f"{shop.shop_id}.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "shop_info": {
                        "shop_id": shop.shop_id,
                        "shop_name": shop.shop_name,
                        "region": "VN",
                        "status": "NORMAL",
                        "rating_star": shop.rating_star,
                    },
                    "order_list": [{k: v for k, v in o.items() if k != "buyer"} for o in orders],
                    "escrow_detail": [
                        {
                            "order_sn": x["order_sn"],
                            "order_income": {
                                k: v for k, v in x.items() if k not in ("order_sn", "create_time")
                            },
                        }
                        for x in income
                    ],
                    "return_list": returns,
                },
                f,
                ensure_ascii=False,
            )
    write_csv(
        out / "shops.csv",
        list(asdict(shops[0]).keys()),
        [list(asdict(s).values()) for s in shops],
    )
    print(f"{len(shops)} shop, {args.weeks} tuần, tại {out}")


if __name__ == "__main__":
    main()
