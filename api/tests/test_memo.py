from copy import deepcopy

import pytest

from services.underwriting.memo import CodeMemoWriter, MemoComposer, default_validator
from services.underwriting.models import DecisionInput, Plan, WorkflowError
from services.underwriting.repository import MemoryCaseRepository
from services.underwriting.risk import BaselineRiskModel
from services.underwriting.service import UnderwritingService
from services.underwriting.sources import (
    FixedCicSource,
    InternalLosAdapter,
    seller_book_source,
)
from tests.test_underwriting import NOW, plan


def _service(writer) -> UnderwritingService:
    return UnderwritingService(
        MemoryCaseRepository(),
        seller_book_source(NOW),
        BaselineRiskModel(),
        FixedCicSource(),
        InternalLosAdapter(),
        clock=lambda: NOW,
        memo=MemoComposer(writer, default_validator()),
    )


def test_memo_facts_are_code_rendered():
    case = _service(CodeMemoWriter()).create(Plan(**plan()), "memo-code")
    doc = case.documents[-1]
    assert case.status == "PENDING_REVIEW"
    assert (doc["generator"], doc["attempt"], doc["validated"]) == ("CODE", 1, True)
    assert doc["model"] is None
    gap = next(s for s in doc["seller_explanation"] if s["template"] == "gap")
    e5 = next(e for e in case.evidence if e["evidence_id"] == gap["evidence_id"])
    assert gap["claims"] == {"gap": e5["payload"]["gap"]}
    assert gap["text"] == f"Hệ thống tính khoảng thiếu vốn là {e5['payload']['gap']} VND."
    assert gap["semantic_frame"]["actor"] == "cashflow_engine"
    assert gap["semantic_frame"]["roles"] == gap["claims"]


def test_seller_explanation_has_three_code_owned_fact_sentences():
    case = _service(CodeMemoWriter()).create(Plan(**plan()), "memo-seller")
    seller = case.documents[-1]["seller_explanation"]
    assert [item["template"] for item in seller] == ["gap", "proposal", "deduction"]
    proposal = seller[1]
    assert proposal["semantic_frame"]["relation"] == "proposes_loan_terms"
    assert proposal["semantic_frame"]["units"] == {
        "amount_proposed": "VND",
        "tenor_weeks": "WEEK",
    }
    assert "Hệ thống đề xuất" in proposal["text"]
    assert "muốn vay" not in proposal["text"].lower()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace("90000000 VND trong 8 tuần", "8 VND trong 90000000 tuần"),
        lambda text: text.replace("Hệ thống đề xuất", "Seller muốn vay"),
        lambda text: "Hệ thống đề xuất chín mươi triệu đồng trong tám tuần.",
        lambda text: text + " Phí xử lý là 2 phần trăm.",
        lambda text: text + " Hồ sơ đã chấp thuận.",
    ],
)
def test_semantic_validator_rejects_adversarial_fact_rewrites(mutate):
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "memo-adversarial")
    item = deepcopy(case.documents[-1]["seller_explanation"][1])
    item["text"] = mutate(item["text"])
    errors = default_validator().sentence_errors(case, item, "seller_explanation")
    assert "MEMO_SEMANTIC_MISMATCH" in errors


def test_decision_revalidates_code_memo():
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "memo-decide")
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="ok")
    assert service.decide(case.case_id, request, "uw", "d1").status == "ACCEPTED"


def test_tampered_llm_text_blocks_decision():
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "llm-tamper")
    stored = service.repository._cases[case.case_id]
    stored.documents[-1]["underwriter_memo"][1]["text"] += " Thêm 5.000.000 VND."
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="ok")
    with pytest.raises(WorkflowError, match="MEMO_NUMBER_NOT_IN_EVIDENCE"):
        service.decide(case.case_id, request, "uw", "d1")


def test_missing_locked_slot_blocks_decision():
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "memo-missing-slot")
    stored = service.repository._cases[case.case_id]
    stored.documents[-1]["seller_explanation"].pop()
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="ok")
    with pytest.raises(WorkflowError, match="MEMO_SLOT_MISMATCH"):
        service.decide(case.case_id, request, "uw", "d1")


def _legacy(document: dict) -> None:
    for audience in ("underwriter_memo", "seller_explanation"):
        for item in document[audience]:
            item.pop("semantic_frame")
            item["text"] = item["text"].replace("Hệ thống đề xuất", "Đề xuất")
    document["seller_explanation"].pop()


def test_memo_written_before_semantic_frames_stays_decidable():
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "memo-legacy")
    _legacy(service.repository._cases[case.case_id].documents[-1])
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="ok")
    assert service.decide(case.case_id, request, "uw", "d1").status == "ACCEPTED"


def test_legacy_memo_still_checks_numbers():
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "memo-legacy-tamper")
    stored = service.repository._cases[case.case_id].documents[-1]
    _legacy(stored)
    stored["underwriter_memo"][1]["text"] += " Thêm 5.000.000 VND."
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="ok")
    with pytest.raises(WorkflowError, match="MEMO_NUMBER_NOT_IN_EVIDENCE"):
        service.decide(case.case_id, request, "uw", "d1")


def test_partially_framed_memo_is_not_treated_as_legacy():
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "memo-partial")
    stored = service.repository._cases[case.case_id].documents[-1]
    stored["seller_explanation"][1].pop("semantic_frame")
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="ok")
    with pytest.raises(WorkflowError, match="MEMO_SEMANTIC_MISMATCH"):
        service.decide(case.case_id, request, "uw", "d1")
