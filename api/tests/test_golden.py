import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import config
from policy.engine import PolicyConfig
from policy.rules import RULES
from services.cashflow import ROUND_UNIT, CashFlowConfig
from services.underwriting.memo import AUDIENCES, MemoConfig, default_validator
from services.underwriting.models import Plan, WorkflowError

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"riverbank_eval_{name}", ROOT / "eval" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


score = _load("score")
inject = _load("inject")

GOLDEN = ROOT / "eval" / "golden"
LLM = GOLDEN / "llm"
UNDERWRITING_RULES = [r.rule_id for r in RULES]


CASES = score.golden_cases(GOLDEN)


def _expected_blocks(case: dict) -> list[dict]:
    blocks = []
    if "expected" in case:
        blocks.append(case["expected"])
    for variant in case.get("variants", []):
        blocks.append(variant["expected"])
    return blocks


@pytest.mark.parametrize("case_id,inp,expected", CASES, ids=[row[0] for row in CASES])
def test_worked_example(case_id: str, inp: dict, expected: dict):
    got = score.score_case(inp)
    for key, want in expected.items():
        if key == "balances":
            by_week = {row["week"]: row for row in got["balances"]}
            for row in want:
                assert by_week[row["week"]] == row, case_id
        else:
            assert got[key] == want, f"{case_id}.{key}: {got.get(key)!r}"


@pytest.mark.parametrize(
    "case_id,inp",
    [(row[0], row[1]) for row in CASES],
    ids=[row[0] for row in CASES],
)
def test_case_stops_before_a_human_decision(case_id: str, inp: dict):
    got = score.score_case(inp)
    assert got["status"] not in {"ACCEPTED", "REJECTED"}, case_id
    assert got["auto_rejected"] is False
    if got["status"] in {"NO_LOAN_NEEDED", "BLOCKED_INPUT", "INVALID_INPUT"}:
        assert got["cic_called"] is False
        assert got["pd_scored"] is False
        assert got["rules_evaluated"] == []
    if got["status"] in {"BLOCKED_INPUT", "INVALID_INPUT"}:
        assert got["underwriting_started"] is False
    if got["gap"] == 0:
        assert got["status"] == "NO_LOAN_NEEDED"
    if isinstance(got["gap"], int) and got["gap"] > 0:
        assert got["cic_called"] is True
        assert got["pd_scored"] is True
        assert got["underwriting_started"] is True
        assert got["rules_evaluated"] == UNDERWRITING_RULES


def _field_bound(model, name: str, attr: str):
    for item in model.model_fields[name].metadata:
        if hasattr(item, attr):
            return getattr(item, attr)
    raise AssertionError(f"{model.__name__}.{name} thiếu {attr}")


def test_parameters_mirror_code():
    cfg = score.parameters()
    cash, policy, cap = cfg["cashflow"], cfg["policy"], cfg["input_hard_cap"]
    cf, pc = CashFlowConfig(), PolicyConfig()
    assert float(cash["stress_factor"]) == cf.stress_factor
    assert float(cash["buffer_factor"]) == cf.buffer_factor
    assert float(cash["buffer_cap"]) == cf.buffer_cap
    assert cash["amount_rounding_vnd"] == ROUND_UNIT
    assert cash["tenor_buffer_weeks"] == cf.tenor_buffer_weeks
    assert cash["tenor_extend_step_weeks"] == cf.tenor_extend_step
    assert cash["max_tenor_weeks"] == cf.tenor_max_weeks
    assert float(cash["deduction_cap_ratio"]) == cf.deduction_cap_of_stress_payout
    assert cash["payout_start_week"] == cf.payout_start_week
    assert cash["extra_weeks"] == cf.extra_weeks
    assert cash["min_history_weeks"] == cf.min_history_weeks
    assert policy["max_amount_vnd"] == pc.max_amount
    assert policy["min_shop_age_months"] == pc.min_shop_age_months
    assert float(policy["max_return_rate"]) == pc.max_return_rate
    assert policy["min_tenor_weeks"] == pc.min_tenor_weeks
    assert policy["max_tenor_weeks"] == pc.max_tenor_weeks
    assert float(policy["doc_required_multiple"]) == pc.doc_required_multiple
    assert policy["max_cic_group"] == pc.max_cic_group
    ratio = float(policy["max_outstanding_to_monthly_payout"])
    assert ratio == pc.max_outstanding_to_monthly_payout
    assert float(policy["deduction_cap_of_stress_payout"]) == pc.deduction_cap_of_stress_payout
    assert policy["e_lending_cap_vnd"] == pc.e_lending_cap
    assert policy["max_pd_bps"] == pc.max_pd_bps
    assert round(float(cfg["statement"]["mismatch_ratio"]) * 10_000) == pc.statement_mismatch_bps
    assert cap["max_requested_amount_vnd"] == _field_bound(Plan, "requested_amount", "le")
    assert cap["max_sell_weeks"] == _field_bound(Plan, "sell_weeks", "le")
    assert cap["max_supplier_rows"] == _field_bound(Plan, "supplier_schedule", "max_length")
    assert cap["max_note_chars"] == _field_bound(Plan, "note", "max_length")
    assert cfg["memo"]["max_attempts"] == MemoConfig().max_attempts
    assert float(cfg["guardrail"]["block_probability"]) == config.GUARD_BLOCK_PROBABILITY


def test_rule_ids_match_policy_doc():
    text = (ROOT / "policy" / "policy.md").read_text(encoding="utf-8")
    for rule in RULES:
        assert f"### {rule.rule_id} " in text, rule.rule_id


def test_case_wording_has_no_forbidden_lexicon():
    for path in GOLDEN.glob("*.json"):
        case = json.loads(path.read_text(encoding="utf-8"))
        if "wording" in case:
            assert score.lexicon_hits(case["wording"]) == [], path.name


def test_forbidden_lexicon_comes_from_guardrails():
    words = score.forbidden_words()
    assert "gian lận" in words
    assert "lừa đảo" in words
    assert "fraud" in words


@pytest.mark.parametrize("path", sorted(LLM.glob("*.json")), ids=lambda path: path.stem)
def test_memo_guard(path: Path):
    case = json.loads(path.read_text(encoding="utf-8"))
    assert score.score_memo(case) == case["expected"]


def test_studio_eval_report_matches_golden():
    committed = json.loads(score.REPORT.read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(score.build_report())), "chạy make eval-report"


def _memo_sentences() -> list[tuple]:
    rows = []
    for case_id, inp, _ in CASES:
        try:
            case = score.golden_service(inp).create(score.golden_plan(inp), case_id)
        except (ValidationError, WorkflowError):
            continue
        for audience in AUDIENCES:
            rows += [(case, item) for item in case.documents[-1][audience]]
    return rows


def test_memo_validator_catches_injected_errors():
    validator, missed, total = default_validator(), [], 0
    for case, item in _memo_sentences():
        assert validator.sentence_errors(case, item) == [], (case.case_id, item["text"])
        for kind, text in inject.mutations(item["text"]):
            total += 1
            if not validator.sentence_errors(case, {**item, "text": text}):
                missed.append((case.case_id, kind, text))
    assert total > 0
    assert missed == []
