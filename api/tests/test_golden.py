import json
from pathlib import Path

import pytest

from policy import PolicyInput, default_engine
from services.cashflow import CashFlowEngine, CashFlowInput

GOLDEN = Path(__file__).resolve().parents[2] / "eval" / "golden"
CASES = sorted(GOLDEN.glob("*.json"))


@pytest.mark.parametrize("path", CASES, ids=[p.stem for p in CASES])
def test_golden_case(path: Path):
    case = json.loads(path.read_text(encoding="utf-8"))
    r = CashFlowEngine().compute(CashFlowInput(**case["input"]))
    for key, want in case["expected"].items():
        if key == "policy_hard_fail":
            out = default_engine().evaluate(
                PolicyInput(
                    amount=r.amount_proposed,
                    tenor_weeks=r.tenor_weeks,
                    weekly_deduction=r.weekly_deduction,
                    payout_stress=r.payout_stress,
                    platform_supported_amount=r.amount_proposed,
                    shop_age_months=12,
                    return_rate=0.05,
                    cic_group=1,
                    cic_outstanding=0,
                )
            )
            assert out.hard_fail == want, out.failed_rule_ids
        else:
            assert getattr(r, key) == want, f"{case['case_id']}.{key}"
