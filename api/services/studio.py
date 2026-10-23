import json
from dataclasses import asdict
from pathlib import Path

from pydantic import BaseModel

from policy.rules import RULES
from services.underwriting.models import WorkflowError
from services.underwriting.service import ENGINE_VERSION, POLICY_VERSION, UnderwritingService

EVAL_REPORT = Path(__file__).resolve().parents[1] / "studio" / "eval_report.json"


class RuleInfo(BaseModel):
    rule_id: str
    hard: bool
    order: int


class StudioPolicy(BaseModel):
    policy_version: str
    policy_hash: str
    engine_version: str
    policy_config: dict
    cashflow_config: dict
    rules: list[RuleInfo]
    risk_model: dict
    cic_gate: dict


class EvalCase(BaseModel):
    case_id: str
    passed: bool
    mismatched_keys: list[str]
    expected_status: str | None
    status: str
    gap: int | None
    amount_proposed: int | None
    cic_called: bool
    pd_scored: bool


class EvalSummary(BaseModel):
    total: int
    passed: int
    failed: int


class EvalReport(BaseModel):
    golden_digest: str
    summary: EvalSummary
    cases: list[EvalCase]


def policy_snapshot(service: UnderwritingService) -> dict:
    return {
        "policy_version": POLICY_VERSION,
        "policy_hash": service.policy_hash,
        "engine_version": ENGINE_VERSION,
        "policy_config": asdict(service.policy.cfg),
        "cashflow_config": asdict(service.engine.cfg),
        "rules": [
            {"rule_id": rule.rule_id, "hard": rule.hard, "order": order}
            for order, rule in enumerate(RULES, start=1)
        ],
        "risk_model": service.risk.describe(),
        "cic_gate": service.cic_gate.describe(),
    }


def eval_report(path: Path = EVAL_REPORT) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise WorkflowError("EVAL_REPORT_MISSING", 503) from None
