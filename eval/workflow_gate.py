import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

import qa_score  # noqa: E402
import score  # noqa: E402

from services.copilot.advisor import FactsReply, case_facts  # noqa: E402
from services.underwriting.models import Case  # noqa: E402


def trajectory_errors(case: Case | None, actual: dict) -> list[str]:
    if case is None:
        return [] if not actual["underwriting_started"] else ["CASE_MISSING"]
    errors = []
    queried = False
    for run in case.runs:
        names = [span["step"] for span in run.get("steps", [])]
        expected = ["CASHFLOW"]
        if run["cashflow"]["gap"]:
            expected += ["RISK"]
            if not queried:
                expected += ["CIC_GATE", "CIC_INQUIRY"]
                queried = True
            expected += ["POLICY"]
        expected += ["MEMO"]
        if names != expected:
            errors.append("TRAJECTORY_MISMATCH")
    if any(event["next_status"] in {"ACCEPTED", "REJECTED"} for event in case.events):
        errors.append("HUMAN_BOUNDARY_VIOLATION")
    return errors


def run() -> dict:
    labeled, rows = qa_score.labels(), []
    for case_id, inp, expected in score.golden_cases():
        try:
            actual, case = score.execute_case(inp, case_id=case_id)
            errors = [f"EXPECTED:{key}" for key in score.mismatches(actual, expected)]
            errors += trajectory_errors(case, actual)
            if case:
                questions = [
                    (question, label)
                    for (name, question), label in labeled.items()
                    if name == case_id
                ]
                if not questions:
                    errors.append("QA_LABEL_MISSING")
                for question, label in questions:
                    text, _ = FactsReply().compose(case_facts(case), question)
                    errors += qa_score.errors(label, case.status, text)
        except Exception as exc:
            errors = [f"EXECUTION:{type(exc).__name__}"]
        rows.append({"case_id": case_id, "passed": not errors, "errors": errors})
    return {"passed": bool(rows) and all(row["passed"] for row in rows), "cases": rows}


def main() -> int:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
