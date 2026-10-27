import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))

import llm_eval  # noqa: E402
import memo_gate  # noqa: E402
import postgres_gate  # noqa: E402
import qa_score  # noqa: E402
import score  # noqa: E402
import workflow_gate  # noqa: E402


def test_llm_eval_uses_final_statement_states():
    for case_id, inp, expected in score.golden_cases():
        if "statement" in inp:
            assert llm_eval.golden_case(case_id, inp).status == expected["status"]


def test_quality_gate_rejects_missing_answers_judge_errors_and_critical_mismatches():
    ask = {
        "answers": 5,
        "expected_answers": 5,
        "unavailable": [],
        "template_fallback_rate": 0.0,
        "quality_failures": [],
    }
    judge = {"judged": 5, "errors": 0, "flags": {}}
    assert llm_eval.quality_errors({"copilot_ask": ask, "jev_judge": judge}) == []
    assert llm_eval.quality_errors({"copilot_ask": {**ask, "answers": 0}})
    assert llm_eval.quality_errors({"copilot_ask": ask, "jev_judge": {**judge, "errors": 1}})
    assert llm_eval.quality_errors(
        {"copilot_ask": {**ask, "quality_failures": ["wrong actor"]}, "jev_judge": judge}
    )


def test_judge_counts_only_success_and_includes_fallback(monkeypatch):
    judge = llm_eval.JevJudge()
    monkeypatch.setattr(judge, "flags", Mock(side_effect=[[], RuntimeError("judge unavailable")]))
    rows = [{"source": source, "section": "copilot_ask"} for source in ("llm", "template")]
    got = judge.run(rows)
    assert got == {"attempted": 2, "judged": 1, "errors": 1, "flags": {}}


def test_empty_memo_gate_returns_nonzero(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["memo_gate.py", "--case", "MISSING-CASE"])
    assert memo_gate.main() == 1


def test_llm_main_returns_nonzero_when_section_cannot_run(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["llm_eval.py", "--section", "ask", "--out", str(tmp_path)])
    monkeypatch.setattr(llm_eval, "AskSection", Mock(side_effect=RuntimeError("unavailable")))
    assert llm_eval.main() == 1
    assert (tmp_path / "report.json").exists()


def test_gate_detects_omitted_step_and_independent_qa_mismatch():
    _, inp, _ = next(row for row in score.golden_cases() if row[0] == "FX-B")
    actual, case = score.execute_case(inp)
    assert workflow_gate.trajectory_errors(case, actual) == []
    case.runs[0]["steps"] = [s for s in case.runs[0]["steps"] if s["step"] != "CIC_INQUIRY"]
    assert workflow_gate.trajectory_errors(case, actual) == ["TRAJECTORY_MISMATCH"]
    label = next(v for k, v in qa_score.labels().items() if k[0] == "FX-B" and "bao nhiêu" in k[1])
    assert qa_score.errors(label, "AWAITING_STATEMENT", "Đề xuất 1 VND")
    assert "QA_ACTOR_MISMATCH" in qa_score.errors(
        label, "PENDING_REVIEW", "Seller muốn vay 90.000.000 VND trong 8 tuần."
    )


def test_offline_release_gate_passes_all_golden_and_labeled_qa():
    assert workflow_gate.run()["passed"]


@pytest.mark.parametrize(
    "url", ["", "postgresql://u@db.example/riverbank_test", "postgresql://u@localhost/riverbank"]
)
def test_postgres_gate_refuses_non_test_database(url):
    with pytest.raises(ValueError):
        postgres_gate.validate_connection(url)
