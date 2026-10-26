import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

import score  # noqa: E402


def inputs(path: Path) -> list[tuple[str, dict, dict]]:
    case = json.loads(path.read_text(encoding="utf-8"))
    if "variants" not in case:
        return [(case["case_id"], case["input"], case["expected"])]
    return [
        (
            f"{case['case_id']}-{variant['variant_id']}",
            {**case.get("base_input", {}), **variant["input"]},
            variant["expected"],
        )
        for variant in case["variants"]
    ]


def run(case_id: str, inp: dict, expected: dict) -> dict:
    started = time.perf_counter()
    try:
        actual, case = score.execute_case(inp, case_id=case_id)
    except Exception as exc:  # release report must retain the failed case
        return {
            "case_id": case_id,
            "passed": False,
            "duration_seconds": round(time.perf_counter() - started, 6),
            "errors": [f"{type(exc).__name__}:{exc}"],
            "mismatched_keys": [],
            "attempts": [],
        }

    wrong = score.mismatches(actual, expected)
    documents = list(case.documents) if case is not None else []
    attempts = [
        {
            "attempt": document["attempt"],
            "generator": document["generator"],
            "model": document["model"],
            "validated": document["validated"],
            "errors": document["validation_errors"],
        }
        for document in documents
    ]
    errors = [f"EXPECTED_MISMATCH:{key}" for key in wrong]
    if actual["underwriting_started"] and not documents:
        errors.append("MEMO_MISSING")
    if documents and not documents[-1]["validated"]:
        errors.append("MEMO_NOT_VALIDATED")
    if any(document["validation_errors"] for document in documents):
        errors.append("MEMO_ATTEMPT_ERROR")

    last = documents[-1] if documents else {}
    return {
        "case_id": case_id,
        "passed": not errors,
        "duration_seconds": round(time.perf_counter() - started, 6),
        "status": actual["status"],
        "expected_status": expected.get("status"),
        "mismatched_keys": wrong,
        "errors": list(dict.fromkeys(errors)),
        "attempts": attempts,
        "underwriter_memo": [item["text"] for item in last.get("underwriter_memo", [])],
        "seller_explanation": [item["text"] for item in last.get("seller_explanation", [])],
    }


def summarize(results: list[dict]) -> dict:
    passed = sum(result["passed"] for result in results)
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "memo_documents": sum(len(result["attempts"]) for result in results),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    rows = [
        row
        for path in sorted((ROOT / "eval" / "golden").glob("*.json"))
        for row in inputs(path)
        if not args.case or any(row[0].startswith(selected) for selected in args.case)
    ]
    results = [run(case_id, inp, expected) for case_id, inp, expected in rows]
    report = {"summary": summarize(results), "cases": results}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
