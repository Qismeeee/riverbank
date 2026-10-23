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
        "{disclosure}: {pd_bps} điểm cơ bản, model {model_version}.",
        ["disclosure", "pd_bps", "model_version"],
    ),
    "risk_missing": (
        "Chưa chấm được PD, thiếu dữ liệu: {missing_fields}.",
        ["missing_fields"],
    ),
    "policy": ("Kết quả chính sách: {summary}.", ["summary"]),
    "source": ("Nguồn dữ liệu: {disclosure}.", ["disclosure"]),
    "statement": ("Kết quả đối chiếu sao kê: {message}.", ["message"]),
}


def sentence(template: str, evidence: dict) -> dict:
    text, keys = TEMPLATES[template]
    claims = {key: evidence["payload"][key] for key in keys}
    rendered = {k: ", ".join(v) if isinstance(v, list) else v for k, v in claims.items()}
    return {
        "template": template,
        "text": text.format(**rendered),
        "claims": claims,
        "evidence_id": evidence["evidence_id"],
    }
