import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("riverbank_eval_score", ROOT / "eval" / "score.py")
score = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(score)

GOLDEN = ROOT / "eval" / "golden"
LLM = GOLDEN / "llm"
UNDERWRITING_RULES = ["R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08"]
SLIDE_LOCKS = {
    "amount_proposed": {90_000_000},
    "weekly_deduction": {11_250_000, 12_000_000},
    "payout_warning_threshold": {45_000_000},
}


def _cases() -> list[tuple[str, dict, dict]]:
    rows = []
    for path in sorted(GOLDEN.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if "variants" in case:
            for variant in case["variants"]:
                merged = {**case.get("base_input", {}), **variant["input"]}
                case_id = f"{case['case_id']}-{variant['variant_id']}"
                rows.append((case_id, merged, variant["expected"]))
        else:
            rows.append((case["case_id"], case["input"], case["expected"]))
    return rows


CASES = _cases()


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
    assert got["status"] not in {"ACCEPTED", "REJECTED", "RESTRUCTURED_P7"}, case_id
    assert got["auto_rejected"] is False
    assert got["auto_applied"] is False
    if got["status"] in {"NO_LOAN_NEEDED", "BLOCKED_INPUT"}:
        assert got["cic_called"] is False
        assert got["pd_scored"] is False
        assert got["rules_evaluated"] == []
        assert got["underwriting_started"] is False
    if got["gap"] == 0:
        assert got["status"] == "NO_LOAN_NEEDED"
    if isinstance(got["gap"], int) and got["gap"] > 0:
        assert got["cic_called"] is True
        assert got["pd_scored"] is True
        assert got["underwriting_started"] is True
        assert got["rules_evaluated"] == UNDERWRITING_RULES


def test_parameters_are_the_threshold_source():
    cfg = score.parameters()
    cash = cfg["cashflow"]
    assert cash["amount_rounding_vnd"] == 1_000_000
    assert cash["buffer_factor"] == "1.1"
    assert cash["buffer_cap"] == "1.15"
    assert cash["stress_factor"] == "0.7"
    assert cash["deduction_cap_ratio"] == "0.25"
    assert cash["max_tenor_weeks"] == 24
    assert cfg["routing"]["statement_request_multiple"] == "2.5"
    assert cfg["routing"]["statement_mismatch_ratio"] == "0.15"
    assert cfg["appetite"]["max_return_rate_90d"] == "0.08"
    assert cfg["appetite"]["min_shop_age_months"] == 6
    assert cfg["appetite"]["max_cic_group_pass"] == 1
    assert cfg["input_hard_cap"]["max_amount_requested_vnd"] == 1_000_000_000


def test_labels_do_not_reuse_slide_amounts():
    for path in GOLDEN.glob("*.json"):
        case = json.loads(path.read_text(encoding="utf-8"))
        for block in _expected_blocks(case):
            for key, banned in SLIDE_LOCKS.items():
                assert block.get(key) not in banned, path.name


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
