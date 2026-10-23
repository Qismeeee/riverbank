import hashlib
import json
from copy import deepcopy
from uuid import uuid4

from .models import Case, WorkflowError


def digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def emit(case: Case, run_id: str, code: str, payload: dict, now: str) -> dict:
    item = {
        "evidence_id": str(uuid4()),
        "case_id": case.case_id,
        "run_id": run_id,
        "code": code,
        "schema_version": "1",
        "payload": deepcopy(payload),
        "payload_hash": digest(payload),
        "created_at": now,
    }
    case.evidence.append(deepcopy(item))
    return item


def read_evidence(case: Case, evidence_id: str) -> dict:
    item = next((e for e in case.evidence if e["evidence_id"] == evidence_id), None)
    if item is None:
        raise WorkflowError("EVIDENCE_NOT_FOUND", 404)
    if digest(item["payload"]) != item["payload_hash"]:
        raise WorkflowError("EVIDENCE_HASH_MISMATCH")
    return deepcopy(item)


TEMPLATES = {
    "gap": ("Khoảng thiếu vốn là {gap} VND.", ["gap"]),
    "no_loan": (
        "Không cần vay; ngưỡng cảnh báo payout là {payout_warning_threshold} VND mỗi tuần.",
        ["payout_warning_threshold"],
    ),
    "proposal": (
        "Đề xuất {amount_proposed} VND trong {tenor_weeks} tuần.",
        ["amount_proposed", "tenor_weeks"],
    ),
    "deduction": ("Kỳ trích tối đa là {weekly_deduction} VND mỗi tuần.", ["weekly_deduction"]),
    "risk": (
        "Điểm PD demo là {pd_bps} điểm cơ bản; model {model_version}.",
        ["pd_bps", "model_version"],
    ),
    "policy": ("Kết quả chính sách: {summary}.", ["summary"]),
    "source": ("Nguồn dữ liệu: {disclosure}.", ["disclosure"]),
    "statement": ("Kết quả đối chiếu sao kê: {message}.", ["message"]),
}


def sentence(template: str, evidence: dict) -> dict:
    text, keys = TEMPLATES[template]
    claims = {key: evidence["payload"][key] for key in keys}
    return {
        "template": template,
        "text": text.format(**claims),
        "claims": claims,
        "evidence_id": evidence["evidence_id"],
    }


def validate_sentences(case: Case, sentences: list[dict]) -> None:
    for item in sentences:
        evidence = read_evidence(case, item["evidence_id"])
        expected = sentence(item["template"], evidence)
        if item != expected:
            raise WorkflowError("MEMO_EVIDENCE_MISMATCH")


def build_document(case: Case, run: dict, refs: dict, now: str) -> dict:
    common = [sentence("source", refs["E4"]), sentence("gap", refs["E5"])]
    if run["cashflow"]["gap"] == 0:
        common.append(sentence("no_loan", refs["E5"]))
    else:
        common.extend(sentence(t, refs["E5"]) for t in ("proposal", "deduction"))
    internal = list(common)
    for code, template in (("E6", "risk"), ("E7", "policy"), ("E8", "statement")):
        if code in refs:
            internal.append(sentence(template, refs[code]))
    return {
        "document_id": str(uuid4()),
        "run_id": run["run_id"],
        "attempt": 1,
        "underwriter_memo": internal,
        "seller_explanation": common,
        "validated": False,
        "unfinished": True,
        "validation_errors": [],
        "generator": "TEMPLATE",
        "prompt_version": "templates-v1",
        "created_at": now,
    }
