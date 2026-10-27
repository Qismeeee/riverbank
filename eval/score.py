from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from services import guard
from services.cashflow import CashFlowEngine, CashFlowInput
from services.guard import OutputGuard
from services.underwriting.evidence import digest
from services.underwriting.memo import MemoComposer
from services.underwriting.models import Plan, Statement, WorkflowError
from services.underwriting.repository import MemoryCaseRepository
from services.underwriting.risk import BaselineRiskModel, RiskAssessment, RiskInput
from services.underwriting.service import UnderwritingService
from services.underwriting.sources import (
    CicSource,
    InternalLosAdapter,
    MappingFeatureSource,
)

ROOT = Path(__file__).resolve().parents[1]
PARAMETERS = ROOT / "policy" / "parameters.yaml"
GUARDRAILS = ROOT / "knowledge" / "guardrails.md"
GOLDEN = ROOT / "eval" / "golden"
REPORT = ROOT / "api" / "studio" / "eval_report.json"
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
            "source": "fixed",
            "bureau_group": self.inp.get("cic_group", 1),
            "outstanding": self.inp.get("cic_outstanding", 0),
            "queried_at": now.isoformat(),
        }


class _GoldenRisk(BaselineRiskModel):
    def __init__(self, inp: dict):
        self.calls = 0
        self.overridden = "pd_bps" in inp
        self.pd_bps = inp.get("pd_bps")

    def assess(self, inp: RiskInput) -> RiskAssessment:
        self.calls += 1
        base = super().assess(inp)
        if not self.overridden:
            return base
        if self.pd_bps is None:
            return RiskAssessment(base.model_version, base.source, missing_fields=["pd_bps"])
        return replace(base, pd_bps=int(self.pd_bps))


def _features(inp: dict) -> MappingFeatureSource:
    monday = NOW.date() - timedelta(days=NOW.weekday())
    history = [int(v) for v in inp["payout_weekly_history"]]
    consent = {
        "reference": "golden-consent",
        "source": "seller_book",
        "purpose": "WORKING_CAPITAL_UNDERWRITING",
        "granted_at": (NOW - timedelta(days=1)).isoformat(),
        "expires_at": (NOW + timedelta(days=30)).isoformat(),
        "revoked_at": None,
    }
    features = {
        "seller_id": SELLER,
        "shop_id": SELLER,
        "source": "seller_book",
        "payout_origin": "estimated",
        "disclosure": "payout 12 tuần gần nhất từ sàn",
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


def golden_plan(inp: dict) -> Plan:
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


def golden_service(inp: dict, memo: MemoComposer | None = None) -> UnderwritingService:
    return UnderwritingService(
        MemoryCaseRepository(),
        _features(inp),
        _GoldenRisk(inp),
        _GoldenCic(inp),
        InternalLosAdapter(),
        clock=lambda: NOW,
        memo=memo,
    )


def execute_case(
    inp: dict, memo: MemoComposer | None = None, case_id: str = "golden"
) -> tuple[dict, object | None]:
    out = _blank()
    service = golden_service(inp, memo)
    cic, risk = service.cic, service.risk
    try:
        plan = golden_plan(inp)
    except ValidationError:
        out["status"] = "INVALID_INPUT"
        return out, None
    try:
        case = service.create(plan, case_id)
    except WorkflowError as exc:
        out["status"] = exc.code
        return out, None
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
    return out, case


def score_case(inp: dict) -> dict:
    out, _ = execute_case(inp)
    return out


def golden_cases(directory: Path = GOLDEN) -> list[tuple[str, dict, dict]]:
    rows = []
    for path in sorted(directory.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if "variants" in case:
            for variant in case["variants"]:
                merged = {**case.get("base_input", {}), **variant["input"]}
                rows.append(
                    (f"{case['case_id']}-{variant['variant_id']}", merged, variant["expected"])
                )
        else:
            rows.append((case["case_id"], case["input"], case["expected"]))
    return rows


def mismatches(got: dict, expected: dict) -> list[str]:
    wrong = []
    for key, want in expected.items():
        if key == "balances":
            by_week = {row["week"]: row for row in got["balances"]}
            if any(by_week.get(row["week"]) != row for row in want):
                wrong.append(key)
        elif got.get(key) != want:
            wrong.append(key)
    return wrong


def build_report(directory: Path = GOLDEN) -> dict:
    cases = []
    for case_id, inp, expected in golden_cases(directory):
        got = score_case(inp)
        wrong = mismatches(got, expected)
        cases.append(
            {
                "case_id": case_id,
                "passed": not wrong,
                "mismatched_keys": wrong,
                "expected_status": expected.get("status"),
                "status": got["status"],
                "gap": got["gap"],
                "amount_proposed": got.get("amount_proposed"),
                "cic_called": got["cic_called"],
                "pd_scored": got["pd_scored"],
            }
        )
    passed = sum(c["passed"] for c in cases)
    return {
        "golden_digest": digest(
            {p.name: p.read_text(encoding="utf-8") for p in sorted(directory.glob("*.json"))}
        ),
        "summary": {"total": len(cases), "passed": passed, "failed": len(cases) - passed},
        "cases": cases,
    }


def write_report(path: Path = REPORT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(build_report(), ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


def forbidden_words() -> tuple[str, ...]:
    return guard.forbidden_words(GUARDRAILS)


def lexicon_hits(text: str) -> list[str]:
    return OutputGuard(forbidden_words()).lexicon_hits(text)


def score_memo(case: dict) -> dict:
    evidence_ids = list(case.get("evidence_ids") or [])
    evidence = case.get("evidence") or {}
    cited = evidence_ids and all(evidence_id in evidence for evidence_id in evidence_ids)
    allowed = guard.ints([evidence[e] for e in evidence_ids]) if cited else None
    reasons = OutputGuard(forbidden_words()).check(
        case["sentence"], allowed, case.get("status_target", "")
    )
    if case.get("locked_text") is not None and case["sentence"] != case["locked_text"]:
        reasons.append("semantic_mismatch")
    return {"passed": not reasons, "reasons": reasons}


if __name__ == "__main__":
    write_report()
