import math

import pytest

from services.features import RAW_FIELDS, FeatureBuilder

RAW = dict(
    archetype="fashion",
    shop_age_months=14,
    n_products=80,
    top3_share=0.3,
    rating=4.7,
    return_rate_12w=0.05,
    payout_mean_12w=70_000_000,
    payout_cv_12w=0.2,
    payout_trend=0.85,
    cash_on_hand=120_000_000,
    supplier_amount=200_000_000,
    amount=90_000_000,
    tenor_weeks=8,
    deduction_to_stress=0.2296,
)


def test_columns_are_stable_and_match_vector():
    fb = FeatureBuilder()
    assert fb.columns == fb.BASE + fb.ENGINEERED + [f"arch_{a}" for a in fb.ARCHETYPES]
    assert len(fb.vector(RAW)) == len(fb.columns)


def test_engineered_values():
    r = FeatureBuilder().row(RAW)
    assert r["age_decay"] == pytest.approx(12 / 26)
    assert r["dts_excess"] == pytest.approx(0.0796)
    assert r["deduction_to_payout"] == pytest.approx(90_000_000 / 8 / 70_000_000)
    assert r["cash_cover_ratio"] == pytest.approx(0.6)
    assert r["log_amount"] == pytest.approx(math.log1p(90_000_000))
    assert r["payout_trend_down"] == 1.0
    assert r["arch_fashion"] == 1.0 and r["arch_electronics"] == 0.0


def test_missing_raw_field_is_rejected():
    raw = {k: v for k, v in RAW.items() if k != "cash_on_hand"}
    with pytest.raises(KeyError):
        FeatureBuilder().row(raw)
    assert "cash_on_hand" in RAW_FIELDS
