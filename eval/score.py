from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from services.cashflow import CashFlowEngine, CashFlowInput
from services.underwriting.models import Plan, Statement, WorkflowError
from services.underwriting.repository import MemoryCaseRepository
from services.underwriting.risk import DemoRiskModel, RiskAssessment, RiskInput
from services.underwriting.service import UnderwritingService
from services.underwriting.sources import (
    CicSource,
    MappingFeatureSource,
    MockLosAdapter,
)

ROOT = Path(__file__).resolve().parents[1]
PARAMETERS = ROOT / "policy" / "parameters.yaml"
GUARDRAILS = ROOT / "knowledge" / "guardrails.md"
NOW = datetime(2026, 10, 22, tzinfo=UTC)
SELLER = "golden-seller"


def _coerce(raw: str) -> int | str:
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def _parse_parameters(text: str) -> dict:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, val = line.strip().partition(":")
        val = val.strip().strip('"').strip("'")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if val == "":
            node: dict = {}
            parent[key] = node
            stack.append((indent, node))
        else:
            parent[key] = _coerce(val)
    return root


@lru_cache(maxsize=1)
def parameters() -> dict:
    return _parse_parameters(PARAMETERS.read_text(encoding="utf-8"))


class _GoldenCic(CicSource):
    def __init__(self, inp: dict):
        self.inp = inp
        self.calls = 0

    def inquire(self, seller_id: str, now: datetime) -> dict:
        self.calls += 1
        return {
            "seller_id": seller_id,
            "source": "mock",
            "bureau_group": self.inp.get("cic_group", 1),
            "outstanding": self.inp.get("cic_outstanding", 0),
            "queried_at": now.isoformat(),
        }


class _GoldenRisk(DemoRiskModel):
    def __init__(self):
        self.calls = 0

    def assess(self, inp: RiskInput) -> RiskAssessment:
        self.calls += 1
        return super().assess(inp)


def _features(inp: dict) -> MappingFeatureSource:
    monday = NOW.date() - timedelta(days=NOW.weekday())
    history = [int(v) for v in inp["payout_weekly_history"]]
    consent = {
        "reference": "golden-consent",
        "source": "synthetic",
        "purpose": "WORKING_CAPITAL_UNDERWRITING",
        "granted_at": (NOW - timedelta(days=1)).isoformat(),
        "expires_at": (NOW + timedelta(days=30)).isoformat(),
        "revoked_at": None,
    }
    features = {
        "seller_id": SELLER,
        "shop_id": SELLER,
        "source": "synthetic",
        "payout_origin": "simulated",
        "disclosure": "synthetic; payout mô phỏng",
        "platform_supported_amount": int(inp.get("platform_support_limit", 120_000_000)),
        "shop_age_months": inp.get("shop_age_months", 12),
        "return_rate": inp.get("return_rate_90d", 0.05),
        "weekly_payouts": [
            {
                "week_start": (monday - timedelta(weeks=len(history) - i)).isoformat(),
                "amount": amount,
            }
            for i, amount in enumerate(history)
        ],
        "raw_refs": [],
        "etl_run_id": None,
    }
    return MappingFeatureSource({(SELLER, SELLER): (consent, features)})


def _plan(inp: dict) -> Plan:
    schedule = inp["supplier_schedule"]
    return Plan(
        seller_id=SELLER,
        shop_id=SELLER,
        lot_value=int(inp.get("lot_value") or sum(int(p["amount"]) for p in schedule)),
        cash_on_hand=int(inp["cash_on_hand"]),
        supplier_schedule=schedule,
        sell_weeks=int(inp["sell_weeks"]),
        requested_amount=int(inp["requested_amount"]),
        note=inp.get("note", ""),
    )


def _blank() -> dict:
    return {
        "status": None,
        "reason": None,
        "payout_base": None,
        "payout_stress": None,
        "gap": None,
        "negative_weeks": [],
        "balances": [],
        "amount_proposed": 0,
        "tenor_weeks": 0,
        "weekly_deduction": 0,
        "weekly_deductions": [],
        "tenor_trace": [],
        "payout_warning_threshold": None,
        "warning_at_threshold_min_end": None,
        "warning_below_has_gap": None,
        "cic_called": False,
        "pd_scored": False,
        "pd_bps": None,
        "rules_evaluated": [],
        "failed_rules": [],
        "hard_failed_rules": [],
        "statement_required": False,
        "status_before_statement": None,
        "statement_matched": None,
        "underwriting_started": False,
        "memo_validated": None,
        "auto_rejected": False,
    }


def _warning(inp: dict, threshold: int) -> tuple[int, bool]:
    engine = CashFlowEngine()
    weeks = len(inp["payout_weekly_history"])

    def run(payout: int):
        return engine.compute(
            CashFlowInput(
                cash_on_hand=int(inp["cash_on_hand"]),
                supplier_schedule=inp["supplier_schedule"],
                sell_weeks=int(inp["sell_weeks"]),
                payout_weekly_history=[payout] * weeks,
            )
        )

    at = run(threshold)
    below = run(threshold - 1) if threshold > 0 else at
    return min(row["bal_end"] for row in at.weeks), below.gap > 0


def _read(case, out: dict) -> dict:
    run = case.runs[-1]
    cf = run["cashflow"]
    out.update(
        status=case.status,
        reason=case.reason,
        payout_base=cf["payout_base"],
        payout_stress=cf["payout_stress"],
        gap=cf["gap"],
        negative_weeks=cf["negative_weeks"],
        balances=cf["weeks"],
        amount_proposed=cf["amount_proposed"],
        tenor_weeks=cf["tenor_weeks"],
        weekly_deduction=cf["weekly_deduction"],
        weekly_deductions=cf["weekly_deductions"],
        tenor_trace=[
            {"tenor_weeks": t["tenor"], "weekly_deduction": t["deduction"]}
            for t in cf["tenor_trace"]
        ],
        payout_warning_threshold=cf["payout_warning_threshold"],
        pd_bps=run["risk"]["pd_bps"] if run["risk"] else None,
        underwriting_started=True,
        memo_validated=case.documents[-1]["validated"],
    )
    policy = run["policy"]
    if policy:
        results = policy["results"]
        out["rules_evaluated"] = [r["rule_id"] for r in results]
        out["failed_rules"] = [r["rule_id"] for r in results if not r["passed"]]
        out["hard_failed_rules"] = [r["rule_id"] for r in results if r["hard"] and not r["passed"]]
        first = case.runs[0]["policy"]["results"]
        out["statement_required"] = any(r["rule_id"] == "R05" and not r["passed"] for r in first)
    if run["statement"]:
        out["statement_matched"] = run["statement"]["matched"]
    return out


def score_case(inp: dict) -> dict:
    out = _blank()
    cic, risk = _GoldenCic(inp), _GoldenRisk()
    service = UnderwritingService(
        MemoryCaseRepository(), _features(inp), risk, cic, MockLosAdapter(), clock=lambda: NOW
    )
    try:
        plan = _plan(inp)
    except ValidationError:
        out["status"] = "INVALID_INPUT"
        return out
    try:
        case = service.create(plan, "golden")
    except WorkflowError as exc:
        out["status"] = exc.code
        return out
    _read(case, out)
    if out["payout_warning_threshold"] is not None:
        at_min, below_gap = _warning(inp, out["payout_warning_threshold"])
        out["warning_at_threshold_min_end"] = at_min
        out["warning_below_has_gap"] = below_gap
    statement = inp.get("statement")
    if statement is not None:
        out["status_before_statement"] = case.status
        weeks = case.runs[-1]["input_snapshot"]["features"]["weekly_payouts"]
        period = weeks[-int(statement["weeks"]) :]
        end = date.fromisoformat(period[-1]["week_start"]) + timedelta(days=7)
        case = service.submit_statement(
            case.case_id,
            Statement(
                expected_version=case.version,
                period_start=period[0]["week_start"],
                period_end=end.isoformat(),
                marketplace_total=int(statement["marketplace_total"]),
                document_ref="golden:statement",
            ),
            "golden-underwriter",
        )
        _read(case, out)
    out["cic_called"] = cic.calls > 0
    out["pd_scored"] = risk.calls > 0
    out["auto_rejected"] = case.status == "REJECTED"
    return out


def forbidden_words() -> tuple[str, ...]:
    text = GUARDRAILS.read_text(encoding="utf-8")
    section = text.split("### Từ cấm", 1)[1].split("###", 1)[0]
    return tuple(word.casefold() for word in re.findall(r"`([^`]+)`", section))


def lexicon_hits(text: str) -> list[str]:
    folded = text.casefold()
    return [word for word in forbidden_words() if word in folded]


def _ints(node: object, found: set[int]) -> None:
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, int):
        found.add(node)
    elif isinstance(node, float) and node.is_integer():
        found.add(int(node))
    elif isinstance(node, dict):
        for value in node.values():
            _ints(value, found)
    elif isinstance(node, list):
        for value in node:
            _ints(value, found)


def _numbers(text: str) -> list[int]:
    found = []
    for match in re.finditer(r"\d{1,3}(?:\.\d{3})+|\d+", text):
        found.append(int(match.group().replace(".", "")))
    return found


def _contradicts(sentence: str, status: str) -> bool:
    folded = sentence.casefold()
    approved = ("đã duyệt", "được duyệt", "accepted")
    if status != "ACCEPTED" and any(token in folded for token in approved):
        return True
    rejected = ("đã từ chối", "rejected")
    if status != "REJECTED" and any(token in folded for token in rejected):
        return True
    if "không cần vay" in folded and status != "NO_LOAN_NEEDED":
        return True
    needs_loan = "cần vay" in folded and "không cần vay" not in folded
    return needs_loan and status == "NO_LOAN_NEEDED"


def score_memo(case: dict) -> dict:
    sentence = case["sentence"]
    evidence_ids = list(case.get("evidence_ids") or [])
    evidence = case.get("evidence") or {}
    numbers = _numbers(sentence)
    known: set[int] = set()
    for evidence_id in evidence_ids:
        _ints(evidence.get(evidence_id), known)
    reasons: list[str] = []
    if lexicon_hits(sentence):
        reasons.append("lexicon")
    cited = [evidence_id for evidence_id in evidence_ids if evidence_id not in evidence]
    if numbers and (not evidence_ids or cited):
        reasons.append("missing_evidence")
    elif numbers and not set(numbers) <= known:
        reasons.append("number")
    if _contradicts(sentence, case.get("status_target", "")):
        reasons.append("contradicts_status")
    return {"passed": not reasons, "reasons": reasons}
