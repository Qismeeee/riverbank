import httpx
import pytest

from routers import underwriting as routes
from services.underwriting.gates import GateInput, JevCicGate, state
from services.underwriting.models import CicInquiry, DecisionInput, WorkflowError
from services.underwriting.repository import MemoryCaseRepository
from services.underwriting.risk import BaselineRiskModel
from services.underwriting.service import UnderwritingService
from services.underwriting.sources import FixedCicSource, InternalLosAdapter, seller_book_source
from tests.test_underwriting import NOW, statement_request
from tests.test_underwriting_risk import create


class FakeJev:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, state, questions, timeout):
        self.calls.append((state, questions, timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class CountingCic(FixedCicSource):
    def __init__(self):
        self.calls = 0

    def inquire(self, seller_id, now):
        self.calls += 1
        return super().inquire(seller_id, now)


def noul(p):
    return {"eligible": {"type": "noul", "noul": p}}


def build(jev):
    return UnderwritingService(
        MemoryCaseRepository(),
        seller_book_source(NOW),
        BaselineRiskModel(),
        CountingCic(),
        InternalLosAdapter(),
        clock=lambda: NOW,
        cic_gate=JevCicGate(decide=jev),
    )


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    monkeypatch.setattr(routes.config, "ENV", "local")
    monkeypatch.setattr(routes.config, "WORKFLOW_MODE", "enabled")


def test_eligible_gate_queries_cic_and_records_decision():
    jev = FakeJev(noul(0.8))
    service = build(jev)
    case = create(service)
    cic = case.runs[-1]["cic"]
    assert case.status == "PENDING_REVIEW"
    assert service.cic.calls == 1 and cic["bureau_group"] == 1
    assert cic["gate"] == {
        "gate": "cic",
        "eligible": True,
        "threshold": 0.6,
        "model": "typesafe/jev-1.14",
        "probability": 0.8,
        "error": None,
    }
    assert jev.calls[0][2] == 10.0


@pytest.mark.parametrize(
    "answer, error",
    [
        (noul(0.59), None),
        (httpx.ConnectError("down"), "ConnectError"),
        (noul(1.5), "ValueError"),
        ({}, "KeyError"),
    ],
)
def test_declined_or_failed_gate_skips_cic_and_escalates(answer, error):
    service = build(FakeJev(answer))
    case = create(service)
    run = case.runs[-1]
    r06 = next(r for r in run["policy"]["results"] if r["rule_id"] == "R06")
    assert service.cic.calls == 0
    assert run["cic"]["source"] == "not_queried" and run["cic"]["bureau_group"] is None
    assert run["cic"]["gate"]["eligible"] is False and run["cic"]["gate"]["error"] == error
    assert not r06["passed"] and r06["missing_data"]
    assert (case.status, case.reason) == ("ESCALATED", "HARD_POLICY_FAILURE")


def test_underwriter_cic_inquiry_unblocks_declined_gate():
    service = build(FakeJev(noul(0.3)))
    case = create(service)
    request = CicInquiry(expected_version=case.version, reason="Đã gọi seller xác minh")
    updated = service.inquire_cic(case.case_id, request, "uw-1")
    cic = updated.runs[-1]["cic"]
    assert service.cic.calls == 1 and updated.version == case.version + 1
    assert updated.runs[-1]["trigger"] == "CIC_INQUIRY"
    assert cic["bureau_group"] == 1 and cic["gate"] == case.runs[-1]["cic"]["gate"]
    assert cic["override"]["actor"] == "uw-1"
    assert (updated.status, updated.reason) == ("PENDING_REVIEW", None)
    assert updated.events[-1]["event_type"] == "CIC_INQUIRY_OVERRIDE"
    decision = DecisionInput(
        expected_version=updated.version, decision_type="ACCEPT_PROPOSAL", reason="test"
    )
    assert service.decide(updated.case_id, decision, "uw-1", "accept").status == "ACCEPTED"


def test_cic_inquiry_only_when_gate_skipped_cic():
    service = build(FakeJev(noul(0.9)))
    case = create(service)
    request = CicInquiry(expected_version=case.version, reason="x")
    with pytest.raises(WorkflowError, match="CIC_INQUIRY_NOT_ALLOWED"):
        service.inquire_cic(case.case_id, request, "uw-1")
    assert service.cic.calls == 1


@pytest.mark.parametrize("p", [0.9, 0.2])
def test_statement_case_waits_for_statement_before_gate(p):
    jev = FakeJev(noul(p))
    service = build(jev)
    case = create(service, "ducthinh.tech")
    cic = case.runs[-1]["cic"]
    assert (case.status, case.reason) == ("AWAITING_STATEMENT", "STATEMENT_REQUIRED")
    assert jev.calls == [] and service.cic.calls == 0
    assert cic["source"] == "deferred_until_statement" and cic["gate"] is None
    updated = service.submit_statement(case.case_id, statement_request(case), "uw-1")
    gate_state = " ".join(jev.calls[0][0])
    assert len(jev.calls) == 1 and "Sao kê ngân hàng đã đối chiếu" in gate_state
    assert "140000000" in gate_state and "khớp trong ngưỡng" in gate_state
    assert updated.runs[-1]["cic"]["gate"]["probability"] == p
    if p >= 0.6:
        assert service.cic.calls == 1 and updated.status == "PENDING_REVIEW"
    else:
        assert service.cic.calls == 0 and updated.status == "ESCALATED"
        request = CicInquiry(expected_version=updated.version, reason="Đã gọi seller")
        assert service.inquire_cic(case.case_id, request, "uw-1").status == "PENDING_REVIEW"


def test_counter_after_statement_reuses_gate_snapshot():
    jev = FakeJev(noul(0.9))
    service = build(jev)
    case = create(service, "ducthinh.tech")
    updated = service.submit_statement(case.case_id, statement_request(case), "uw-1")
    decision = DecisionInput(
        expected_version=updated.version,
        decision_type="ACCEPT_COUNTER",
        reason="giảm số tiền",
        final_amount=updated.runs[-1]["cashflow"]["amount_proposed"] - 10_000_000,
        final_tenor_weeks=updated.runs[-1]["cashflow"]["tenor_weeks"],
    )
    countered = service.decide(case.case_id, decision, "uw-1", "counter")
    assert len(jev.calls) == 1 and service.cic.calls == 1
    assert countered.runs[-1]["cic"] == updated.runs[-1]["cic"]


def test_statement_mismatch_reaches_gate_state():
    jev = FakeJev(noul(0.9))
    service = build(jev)
    case = create(service, "ducthinh.tech")
    updated = service.submit_statement(
        case.case_id, statement_request(case, marketplace_total=50_000_000), "uw-1"
    )
    assert "không khớp" in " ".join(jev.calls[0][0])
    assert (updated.status, updated.reason) == ("ESCALATED", "HARD_POLICY_FAILURE")


def test_state_holds_only_computed_numbers_and_labels():
    case = create(build(FakeJev(noul(0.9))))
    run = case.runs[-1]
    features = run["input_snapshot"]["features"]
    text = " ".join(state(GateInput(features, run["cashflow"], run["risk"])))
    assert str(run["cashflow"]["gap"]) in text and str(run["risk"]["pd_bps"]) in text
    for private in (case.seller_id, case.shop_id, case.consent["reference"]):
        assert private not in text


@pytest.mark.parametrize(
    "gate, key, code",
    [("unknown", "k", "INVALID_WORKFLOW_CIC_GATE"), ("jev", "", "OPENROUTER_KEY_REQUIRED")],
)
def test_gate_config_fails_fast(monkeypatch, gate, key, code):
    monkeypatch.setattr(routes.config, "WORKFLOW_CIC_GATE", gate)
    monkeypatch.setattr(routes.config, "OPENROUTER_API_KEY", key)
    routes.get_service.cache_clear()
    try:
        with pytest.raises(WorkflowError) as error:
            routes.get_service()
    finally:
        routes.get_service.cache_clear()
    assert (error.value.code, error.value.status) == (code, 503)
