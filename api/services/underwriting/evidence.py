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
    "gap": ("Hệ thống tính khoảng thiếu vốn là {gap} VND.", ["gap"]),
    "no_loan": (
        "Hệ thống xác định không cần vay; ngưỡng cảnh báo payout là "
        "{payout_warning_threshold} VND mỗi tuần.",
        ["payout_warning_threshold"],
    ),
    "proposal": (
        "Hệ thống đề xuất {amount_proposed} VND trong {tenor_weeks} tuần "
        "dựa trên khoảng thiếu vốn đã tính.",
        ["amount_proposed", "tenor_weeks"],
    ),
    "deduction": (
        "Hệ thống dự kiến trích tối đa {weekly_deduction} VND mỗi tuần.",
        ["weekly_deduction"],
    ),
    "risk": (
        "Mô hình ước tính {disclosure}: {pd_bps} điểm cơ bản, phiên bản {model_version}.",
        ["disclosure", "pd_bps", "model_version"],
    ),
    "risk_missing": (
        "Hệ thống chưa chấm được PD vì thiếu dữ liệu: {missing_fields}.",
        ["missing_fields"],
    ),
    "policy": ("Policy Engine kết luận: {summary}.", ["summary"]),
    "source": ("Hệ thống sử dụng nguồn dữ liệu: {disclosure}.", ["disclosure"]),
    "statement": ("Hệ thống đối chiếu sao kê và kết luận: {message}.", ["message"]),
}

FRAME_SPECS = {
    "gap": ("cashflow_engine", "calculates_gap", {"gap": "VND"}),
    "no_loan": (
        "cashflow_engine",
        "determines_no_loan_and_warning_threshold",
        {"payout_warning_threshold": "VND_PER_WEEK"},
    ),
    "proposal": (
        "cashflow_engine",
        "proposes_loan_terms",
        {"amount_proposed": "VND", "tenor_weeks": "WEEK"},
    ),
    "deduction": (
        "cashflow_engine",
        "calculates_max_weekly_deduction",
        {"weekly_deduction": "VND_PER_WEEK"},
    ),
    "risk": ("risk_model", "estimates_probability_of_default", {"pd_bps": "BPS"}),
    "risk_missing": ("risk_model", "reports_missing_risk_data", {}),
    "policy": ("policy_engine", "evaluates_policy", {}),
    "source": ("underwriting_system", "discloses_data_source", {}),
    "statement": ("underwriting_system", "reconciles_statement", {}),
}


def semantic_frame(template: str, claims: dict, audience: str) -> dict:
    actor, relation, units = FRAME_SPECS[template]
    return {
        "actor": actor,
        "relation": relation,
        "audience": audience,
        "roles": deepcopy(claims),
        "units": units,
    }


def sentence(template: str, evidence: dict, audience: str = "underwriter_memo") -> dict:
    text, keys = TEMPLATES[template]
    claims = {key: evidence["payload"][key] for key in keys}
    rendered = {k: ", ".join(v) if isinstance(v, list) else v for k, v in claims.items()}
    return {
        "template": template,
        "text": text.format(**rendered),
        "claims": claims,
        "semantic_frame": semantic_frame(template, claims, audience),
        "locked": True,
        "evidence_id": evidence["evidence_id"],
    }
