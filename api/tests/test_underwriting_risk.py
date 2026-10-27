from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from main import app
from policy import PolicyConfig
from routers import underwriting as routes
from services.risk import MODEL_DIR
from services.underwriting.models import DecisionInput, WorkflowError
from services.underwriting.repository import MemoryCaseRepository
from services.underwriting.risk import LightGbmRiskModel, RiskInput
from services.underwriting.service import UnderwritingService
from services.underwriting.sources import (
    FixedCicSource,
    InternalLosAdapter,
    seller_book_source,
    validate_features,
)
from tests.test_underwriting import NOW, plan, seller_headers


class FakeScorer:
    meta = {"model_version": "fake-v1"}

    def __init__(self, pd=0.0123, factors=None):
        self.pd = pd
        self.factors = factors or [
            SimpleNamespace(name="payout_cv_12w", shap=0.4, value=0.2),
            SimpleNamespace(name="rating", shap=-0.1, value=4.7),
        ]
        self.calls = []

    def predict(self, raw, top_k=5):
        self.calls.append((raw, top_k))
        return SimpleNamespace(pd=self.pd, factors=self.factors, model_version="fake-v1")


def build(risk, mutate=None):
    source = seller_book_source(NOW)
    if mutate:
        for _, features in source.records.values():
            mutate(features)
    return UnderwritingService(
        MemoryCaseRepository(),
        source,
        risk,
        FixedCicSource(),
        InternalLosAdapter(),
        clock=lambda: NOW,
    )


def create(service, seller="minhchau.shop", **changes):
    app.dependency_overrides[routes.get_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/cases", json=plan(seller, **changes), headers=seller_headers(seller)
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 201, response.text
    return service.repository.get(response.json()["case_id"])


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    monkeypatch.setattr(routes.config, "ENV", "local")
    monkeypatch.setattr(routes.config, "WORKFLOW_MODE", "enabled")


def test_raw_features_follow_case_inputs():
    case = create(build(LightGbmRiskModel(FakeScorer())))
    run = case.runs[-1]
    features, cf = run["input_snapshot"]["features"], run["cashflow"]
    raw = LightGbmRiskModel.raw_features(RiskInput(features, case.plan, cf))
    payouts = [w["amount"] for w in features["weekly_payouts"]]
    assert raw["payout_mean_12w"] == sum(payouts[-12:]) // len(payouts[-12:])
    assert raw["supplier_amount"] == sum(p["amount"] for p in case.plan["supplier_schedule"])
    assert raw["amount"] == cf["amount_proposed"]
    assert raw["tenor_weeks"] == cf["tenor_weeks"]
    assert raw["deduction_to_stress"] == round(cf["weekly_deduction"] / cf["payout_stress"], 4)
    assert raw["return_rate_12w"] == features["return_rate"]
    assert raw["rating"] == features["rating"]


def test_lightgbm_scores_complete_profile():
    scorer = FakeScorer()
    case = create(build(LightGbmRiskModel(scorer, top_k=3)))
    risk = case.runs[-1]["risk"]
    assert case.status == "PENDING_REVIEW" and case.reason is None
    assert risk["source"] == "lightgbm" and risk["model_version"] == "fake-v1"
    assert risk["pd_bps"] == round(scorer.pd * 10_000)
    assert [f["direction"] for f in risk["factors"]] == ["increase", "decrease"]
    assert risk["missing_fields"] == []
    assert scorer.calls[0][1] == 3
    memo = case.documents[-1]["underwriter_memo"]
    assert any(s["template"] == "risk" and risk["disclosure"] in s["text"] for s in memo)


@pytest.mark.parametrize("field", ["n_products", "rating", "archetype"])
def test_missing_profile_escalates_without_scoring(field):
    scorer = FakeScorer()
    case = create(build(LightGbmRiskModel(scorer), lambda f: f.pop(field)))
    risk = case.runs[-1]["risk"]
    assert (case.status, case.reason) == ("ESCALATED", "RISK_DATA_MISSING")
    assert risk["pd_bps"] is None and risk["factors"] == []
    assert risk["missing_fields"] == [field]
    assert scorer.calls == []
    memo = case.documents[-1]["underwriter_memo"]
    assert case.documents[-1]["validated"]
    assert any(s["template"] == "risk_missing" and field in s["text"] for s in memo)


def test_missing_risk_is_approvable_only_with_verification_note():
    service = build(LightGbmRiskModel(FakeScorer()), lambda f: f.pop("rating"))
    case = create(service)
    plain = {"expected_version": 1, "decision_type": "ACCEPT_PROPOSAL", "reason": "test"}
    with pytest.raises(WorkflowError, match="VERIFICATION_NOTE_REQUIRED"):
        service.decide(case.case_id, DecisionInput(**plain), "uw", "no-note")
    noted = DecisionInput(**plain, verification_note="Đã đối chiếu hồ sơ shop")
    accepted = service.decide(case.case_id, noted, "uw", "note")
    assert accepted.status == "ACCEPTED"
    r10 = next(r for r in accepted.decision["policy_results_final"] if r["rule_id"] == "R10")
    assert r10["missing_data"] and not r10["passed"]


def test_hard_failure_outranks_missing_risk():
    case = create(
        build(LightGbmRiskModel(FakeScorer()), lambda f: f.update(rating=None, shop_age_months=1))
    )
    assert (case.status, case.reason) == ("ESCALATED", "HARD_POLICY_FAILURE")


class AmountScorer(FakeScorer):
    def __init__(self, limit, high_bps, low_bps):
        super().__init__()
        self.limit, self.high, self.low = limit, high_bps, low_bps

    def predict(self, raw, top_k=5):
        self.pd = (self.high if raw["amount"] > self.limit else self.low) / 10_000
        return super().predict(raw, top_k)


def test_pd_over_cap_escalates_and_counter_rescores_pd():
    cap = PolicyConfig().max_pd_bps
    service = build(LightGbmRiskModel(AmountScorer(80_000_000, cap + 1, cap)))
    case = create(service)
    r10 = next(r for r in case.runs[-1]["policy"]["results"] if r["rule_id"] == "R10")
    assert (case.status, case.reason) == ("ESCALATED", "HARD_POLICY_FAILURE")
    assert not r10["passed"] and r10["evidence_id"]
    note = {"reason": "test", "verification_note": "Đã kiểm tra"}
    with pytest.raises(WorkflowError, match="HARD_POLICY_FAILURE") as error:
        service.decide(
            case.case_id,
            DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", **note),
            "uw",
            "proposal",
        )
    assert error.value.details == ["R10"]
    counter = DecisionInput(
        expected_version=1,
        decision_type="ACCEPT_COUNTER",
        final_amount=80_000_000,
        final_tenor_weeks=8,
        **note,
    )
    accepted = service.decide(case.case_id, counter, "uw", "counter")
    assert accepted.status == "ACCEPTED"
    assert [run["risk"]["pd_bps"] for run in accepted.runs] == [cap + 1, cap]


def test_missing_risk_outranks_statement_request():
    case = create(
        build(LightGbmRiskModel(FakeScorer()), lambda f: f.pop("top3_share")), "ducthinh.tech"
    )
    assert (case.status, case.reason) == ("ESCALATED", "RISK_DATA_MISSING")


@pytest.mark.parametrize(
    "changes",
    [
        {"rating": 5.5},
        {"top3_share": -0.1},
        {"n_products": 1.5},
        {"n_products": True},
        {"archetype": " "},
        {"archetype": 3},
    ],
)
def test_invalid_profile_rejected(changes):
    _, features = seller_book_source(NOW).records[("minhchau.shop", "minhchau.shop")]
    features.update(changes)
    with pytest.raises(WorkflowError) as error:
        validate_features(features, "minhchau.shop", "minhchau.shop", NOW)
    assert (error.value.code, error.value.status) == ("INVALID_FEATURE_SNAPSHOT", 422)


def test_unknown_risk_config_is_rejected(monkeypatch):
    monkeypatch.setattr(routes.config, "WORKFLOW_RISK", "unknown")
    routes.get_service.cache_clear()
    try:
        with pytest.raises(WorkflowError) as error:
            routes.get_service()
    finally:
        routes.get_service.cache_clear()
    assert error.value.code == "INVALID_WORKFLOW_RISK"


@pytest.mark.skipif(not (MODEL_DIR / "pd_lgbm.txt").exists(), reason="chưa train model PD")
def test_trained_model_scores_demo_case():
    case = create(build(LightGbmRiskModel()))
    risk = case.runs[-1]["risk"]
    assert case.status == "PENDING_REVIEW"
    assert risk["model_version"] == LightGbmRiskModel().scorer.meta["model_version"]
    assert 0 <= risk["pd_bps"] <= 10_000 and 1 <= len(risk["factors"]) <= 5


def test_demo_d_shows_r10_against_demo_b_on_the_same_numbers():
    cap = PolicyConfig().max_pd_bps
    b, d = (
        create(build(LightGbmRiskModel()), seller)
        for seller in ("minhchau.shop", "ngocvy.boutique")
    )
    assert b.runs[-1]["cashflow"]["amount_proposed"] == d.runs[-1]["cashflow"]["amount_proposed"]
    assert b.runs[-1]["risk"]["pd_bps"] <= cap < d.runs[-1]["risk"]["pd_bps"]
    assert (b.status, d.status, d.reason) == ("PENDING_REVIEW", "ESCALATED", "HARD_POLICY_FAILURE")
    failed = [r["rule_id"] for r in d.runs[-1]["policy"]["results"] if not r["passed"]]
    assert failed == ["R10"]
