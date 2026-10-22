from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, median, pstdev

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))

from services.cashflow import CashFlowEngine, CashFlowInput  # noqa: E402

FEE_RATE = 0.125
HISTORY_WEEKS = 12
PAYOUT_TARGET = (20_000_000, 0.6, 5_000_000, 80_000_000)
MAX_LOAN = 300_000_000
ARCHETYPE_RETURN = {"fashion": 0.09, "electronics": 0.05, "fmcg": 0.02, "home": 0.06}
ARCHETYPE_CV = {
    "fashion": (0.25, 0.55),
    "electronics": (0.2, 0.5),
    "fmcg": (0.1, 0.3),
    "home": (0.15, 0.4),
}
SHORT_PAID_DEFAULT_PERIODS = 3


@dataclass
class TrainShop:
    shop_id: int
    archetype: str
    shop_age_months: int
    n_products: int
    top3_share: float
    rating: float
    return_rate_12w: float
    gmv_30d: int
    aov: int
    payout_mean_12w: int
    payout_cv_12w: float
    payout_trend: float
    cash_on_hand: int
    supplier_amount: int
    gap: int
    amount: int
    tenor_weeks: int
    weekly_deduction: int
    deduction_to_stress: float
    shock_weeks: int
    short_paid_periods: int
    label_default: int


class Calibration:
    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = [
            r
            for r in data["shops"]
            if r["segment"] == "sme" and r["gmv_30d"] > 0 and r["aov"]
        ]
        self.by_archetype = {}
        for r in rows:
            self.by_archetype.setdefault(r["archetype"], []).append(r)
        self.all = rows

    def sample(self, rng: random.Random) -> dict:
        return rng.choice(self.all)


class TrainingSetGenerator:
    def __init__(self, calibration: Calibration, seed: int = 7):
        self.cal = calibration
        self.rng = random.Random(seed)
        self.engine = CashFlowEngine()

    def generate(self, n: int) -> tuple[list[TrainShop], list[dict]]:
        shops, weeks = [], []
        i = 0
        while len(shops) < n:
            i += 1
            shop, series = self._one(900_000 + i)
            if shop is None:
                continue
            shops.append(shop)
            weeks += series
        return shops, weeks

    def _one(self, shop_id: int):
        rng = self.rng
        base = self.cal.sample(rng)
        arch = base["archetype"]
        median_p, sigma, lo, hi = PAYOUT_TARGET
        payout_mean = int(
            min(hi, max(lo, rng.lognormvariate(math.log(median_p), sigma)))
        )
        gmv_30d = int(payout_mean * 30 / 7 / (1 - FEE_RATE))
        aov = int(base["aov"] * rng.uniform(0.8, 1.25))
        n_products = max(3, int(base["n_products"] * rng.uniform(0.6, 1.4)))
        top3 = min(0.98, max(0.05, (base["top3_share"] or 0.4) * rng.uniform(0.8, 1.2)))
        rating = round(
            min(5.0, max(3.5, (base["rating"] or 4.6) + rng.gauss(0, 0.15))), 2
        )
        shop_age = rng.randint(2, 60)
        return_rate = max(0.0, ARCHETYPE_RETURN[arch] * rng.lognormvariate(0, 0.4))
        cv = rng.uniform(*ARCHETYPE_CV[arch])

        history = self._series(payout_mean, cv, HISTORY_WEEKS, rng)
        cash = int(payout_mean * rng.uniform(0.3, 1.8))
        supplier = int(payout_mean * rng.uniform(2.0, 4.5))
        result = self.engine.compute(
            CashFlowInput(
                cash_on_hand=cash,
                supplier_schedule=[{"week": 0, "amount": supplier}],
                sell_weeks=6,
                payout_weekly_history=history,
            )
        )
        if not result.loan_needed or result.amount_proposed > MAX_LOAN:
            return None, []

        risk = self._risk_score(
            cv,
            return_rate,
            top3,
            shop_age,
            result.weekly_deduction,
            result.payout_stress,
        )
        repay, shock_weeks, short_paid = self._repayment(
            payout_mean, cv, result, risk, rng
        )
        trend = mean(history[-4:]) / max(1, mean(history[:4]))
        shop = TrainShop(
            shop_id=shop_id,
            archetype=arch,
            shop_age_months=shop_age,
            n_products=n_products,
            top3_share=round(top3, 3),
            rating=rating,
            return_rate_12w=round(return_rate, 4),
            gmv_30d=gmv_30d,
            aov=aov,
            payout_mean_12w=int(mean(history)),
            payout_cv_12w=round(pstdev(history) / max(1, mean(history)), 4),
            payout_trend=round(trend, 4),
            cash_on_hand=cash,
            supplier_amount=supplier,
            gap=result.gap,
            amount=result.amount_proposed,
            tenor_weeks=result.tenor_weeks,
            weekly_deduction=result.weekly_deduction,
            deduction_to_stress=round(
                result.weekly_deduction / max(1, result.payout_stress), 4
            ),
            shock_weeks=shock_weeks,
            short_paid_periods=short_paid,
            label_default=int(short_paid >= SHORT_PAID_DEFAULT_PERIODS),
        )
        series = [
            {"shop_id": shop_id, "week": w, "phase": "history", "payout": p}
            for w, p in enumerate(history)
        ] + [
            {
                "shop_id": shop_id,
                "week": HISTORY_WEEKS + w,
                "phase": "repayment",
                "payout": p,
            }
            for w, p in enumerate(repay)
        ]
        return shop, series

    @staticmethod
    def _series(
        mean_payout: int, cv: float, weeks: int, rng: random.Random
    ) -> list[int]:
        sigma = math.sqrt(math.log(1 + cv * cv))
        mu = math.log(mean_payout) - sigma * sigma / 2
        return [max(0, int(rng.lognormvariate(mu, sigma))) for _ in range(weeks)]

    @staticmethod
    def _risk_score(cv, return_rate, top3, shop_age, deduction, payout_stress) -> float:
        z = (
            -3.9
            + 3.0 * cv
            + 6.0 * return_rate
            + 1.2 * top3
            + 1.5 * (12 / (shop_age + 12))
            + 4.0 * max(0.0, deduction / max(1, payout_stress) - 0.15)
        )
        return 1 / (1 + math.exp(-z))

    def _repayment(self, mean_payout, cv, result, risk, rng):
        payouts = self._series(mean_payout, cv, result.tenor_weeks, rng)
        shock_weeks = 0
        if rng.random() < risk:
            start = rng.randint(0, max(0, result.tenor_weeks - 2))
            length = rng.randint(2, 6)
            factor = rng.uniform(0.25, 0.65)
            for w in range(start, min(result.tenor_weeks, start + length)):
                payouts[w] = int(payouts[w] * factor)
                shock_weeks += 1
        cap_ratio = 0.25
        short = run = 0
        for p in payouts:
            if p * cap_ratio < result.weekly_deduction:
                run += 1
                short = max(short, run)
            else:
                run = 0
        return payouts, shock_weeks, short


def write(shops: list[TrainShop], weeks: list[dict], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "train_shops.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(shops[0]).keys()))
        w.writeheader()
        w.writerows(asdict(s) for s in shops)
    with open(out / "train_weeks.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["shop_id", "week", "phase", "payout"])
        w.writeheader()
        w.writerows(weeks)


def summary(shops: list[TrainShop]) -> dict:
    n = len(shops)
    rate = sum(s.label_default for s in shops) / n
    cvs = sorted(s.payout_cv_12w for s in shops)
    lo, hi = cvs[n // 3], cvs[2 * n // 3]
    by_cv = {
        "low": mean(s.label_default for s in shops if s.payout_cv_12w <= lo),
        "mid": mean(s.label_default for s in shops if lo < s.payout_cv_12w <= hi),
        "high": mean(s.label_default for s in shops if s.payout_cv_12w > hi),
    }
    return {
        "n": n,
        "default_rate": round(rate, 3),
        "default_by_cv_tercile": {k: round(v, 3) for k, v in by_cv.items()},
        "median_amount": int(median(s.amount for s in shops)),
        "median_tenor": int(median(s.tenor_weeks for s in shops)),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument(
        "--calibration", default=str(Path(__file__).parent / "calibration.json")
    )
    ap.add_argument("--out", default=str(ROOT / "data" / "synth" / "train"))
    a = ap.parse_args(argv)
    gen = TrainingSetGenerator(Calibration(Path(a.calibration)), a.seed)
    shops, weeks = gen.generate(a.n)
    write(shops, weeks, Path(a.out))
    print(json.dumps(summary(shops), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
