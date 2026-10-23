from __future__ import annotations

import math
from statistics import mean, pstdev

WEEKS_PER_MONTH = 4.33
DEDUCTION_STRESS_FLOOR = 0.15
PAYOUT_WINDOW_WEEKS = 12
TREND_WEEKS = 4

RAW_FIELDS = [
    "archetype",
    "shop_age_months",
    "n_products",
    "top3_share",
    "rating",
    "return_rate_12w",
    "payout_mean_12w",
    "payout_cv_12w",
    "payout_trend",
    "cash_on_hand",
    "supplier_amount",
    "amount",
    "tenor_weeks",
    "deduction_to_stress",
]
LEAK_FIELDS = ["shock_weeks", "short_paid_periods", "label_default"]


def payout_profile(history: list[int]) -> dict[str, float]:
    window = history[-PAYOUT_WINDOW_WEEKS:]
    if not window:
        raise ValueError("cần lịch sử payout")
    average = mean(window)
    head, tail = window[:TREND_WEEKS], window[-TREND_WEEKS:]
    return {
        "payout_mean_12w": int(average),
        "payout_cv_12w": round(pstdev(window) / max(1, average), 4),
        "payout_trend": round(mean(tail) / max(1, mean(head)), 4),
    }


class FeatureBuilder:
    ARCHETYPES = ["fashion", "electronics", "fmcg", "home"]
    BASE = [
        "shop_age_months",
        "n_products",
        "top3_share",
        "rating",
        "return_rate_12w",
        "payout_mean_12w",
        "payout_cv_12w",
        "payout_trend",
        "amount",
        "tenor_weeks",
        "deduction_to_stress",
    ]
    ENGINEERED = [
        "age_decay",
        "dts_excess",
        "log_amount",
        "log_payout",
        "loan_to_monthly_payout",
        "deduction_to_payout",
        "cash_weeks_of_deduction",
        "cash_cover_ratio",
        "tenor_x_cv",
        "payout_trend_down",
    ]

    @property
    def columns(self) -> list[str]:
        return self.BASE + self.ENGINEERED + [f"arch_{a}" for a in self.ARCHETYPES]

    def row(self, raw: dict) -> dict[str, float]:
        missing = [f for f in RAW_FIELDS if f not in raw]
        if missing:
            raise KeyError(f"thiếu trường đầu vào: {missing}")
        age = float(raw["shop_age_months"])
        payout = max(1.0, float(raw["payout_mean_12w"]))
        amount = float(raw["amount"])
        tenor = max(1.0, float(raw["tenor_weeks"]))
        cash = float(raw["cash_on_hand"])
        supplier = max(1.0, float(raw["supplier_amount"]))
        dts = float(raw["deduction_to_stress"])
        cv = float(raw["payout_cv_12w"])
        weekly = amount / tenor
        out = {k: float(raw[k]) for k in self.BASE}
        out.update(
            age_decay=12.0 / (age + 12.0),
            dts_excess=max(0.0, dts - DEDUCTION_STRESS_FLOOR),
            log_amount=math.log1p(amount),
            log_payout=math.log1p(payout),
            loan_to_monthly_payout=amount / (payout * WEEKS_PER_MONTH),
            deduction_to_payout=weekly / payout,
            cash_weeks_of_deduction=cash / max(1.0, weekly),
            cash_cover_ratio=cash / supplier,
            tenor_x_cv=tenor * cv,
            payout_trend_down=float(float(raw["payout_trend"]) < 0.9),
        )
        for a in self.ARCHETYPES:
            out[f"arch_{a}"] = float(raw["archetype"] == a)
        return out

    def vector(self, raw: dict) -> list[float]:
        r = self.row(raw)
        return [r[c] for c in self.columns]

    def frame(self, df):
        import pandas as pd

        rows = [self.row(r) for r in df.to_dict("records")]
        return pd.DataFrame(rows, columns=self.columns, index=df.index)
