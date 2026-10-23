import pytest

from services.risk import MODEL_DIR, RiskModel

pytestmark = pytest.mark.skipif(
    not (MODEL_DIR / "pd_lgbm.txt").exists(), reason="chưa train: python eval/train_pd.py"
)

CASE_B = dict(
    archetype="fashion",
    shop_age_months=14,
    n_products=80,
    top3_share=0.3,
    rating=4.7,
    return_rate_12w=0.05,
    payout_mean_12w=70_000_000,
    payout_cv_12w=0.2,
    payout_trend=1.0,
    cash_on_hand=120_000_000,
    supplier_amount=200_000_000,
    amount=90_000_000,
    tenor_weeks=8,
    deduction_to_stress=11_250_000 / 49_000_000,
)


def test_predict_returns_pd_and_top_factors():
    r = RiskModel.load().predict(CASE_B)
    assert 0.0 <= r.pd <= 1.0 and len(r.factors) == 5
    assert {f.name for f in r.factors} <= set(RiskModel.load().features)
    assert all(f.direction in ("tăng rủi ro", "giảm rủi ro") for f in r.factors)


def test_pd_moves_in_the_documented_direction():
    model = RiskModel.load()
    base = model.predict(CASE_B).pd
    risky = model.predict(
        {**CASE_B, "payout_cv_12w": 0.55, "return_rate_12w": 0.2, "shop_age_months": 3}
    ).pd
    safe = model.predict(
        {**CASE_B, "payout_cv_12w": 0.1, "return_rate_12w": 0.01, "shop_age_months": 48}
    ).pd
    assert safe < base < risky


def test_unknown_archetype_is_tolerated():
    r = RiskModel.load().predict({**CASE_B, "archetype": "khac"})
    assert 0.0 <= r.pd <= 1.0
