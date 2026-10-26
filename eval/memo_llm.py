import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

import score  # noqa: E402

from services.underwriting.memo import build_composer  # noqa: E402


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


def run(case_id: str, inp: dict, expected: dict, writer: str) -> dict:
    started = time.perf_counter()
    try:
        actual, case = score.execute_case(inp, build_composer(writer), case_id)
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
            "usage": document.get("usage") or {},
            "llm_seconds": document.get("llm_seconds", 0),
        }
        for document in documents
    ]
    errors = [f"EXPECTED_MISMATCH:{key}" for key in wrong]
    if actual["underwriting_started"] and not documents:
        errors.append("MEMO_MISSING")
    if documents and not documents[-1]["validated"]:
        errors.append("MEMO_NOT_VALIDATED")
    if any(document["generator"] == "TEMPLATE" for document in documents):
        errors.append("MEMO_FALLBACK_USED")
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
        "seller_explanation": [
            item["text"] for item in last.get("seller_explanation", [])
        ],
    }


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def summarize(
    results: list[dict], input_price_per_million: float, output_price_per_million: float
) -> dict:
    attempts = [attempt for result in results for attempt in result["attempts"]]
    input_tokens = sum(
        int((attempt.get("usage") or {}).get("prompt_tokens", 0))
        for attempt in attempts
    )
    output_tokens = sum(
        int((attempt.get("usage") or {}).get("completion_tokens", 0))
        for attempt in attempts
    )
    latencies = [
        float(attempt["llm_seconds"]) for attempt in attempts if attempt["llm_seconds"]
    ]
    passed = sum(result["passed"] for result in results)
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "fallbacks": sum(attempt["generator"] == "TEMPLATE" for attempt in attempts),
        "memo_documents": len(attempts),
        "llm_calls": len(latencies),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": round(
            input_tokens * input_price_per_million / 1_000_000
            + output_tokens * output_price_per_million / 1_000_000,
            6,
        ),
        "llm_latency_p50_seconds": round(percentile(latencies, 0.5), 6),
        "llm_latency_p95_seconds": round(percentile(latencies, 0.95), 6),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--writer", default="llm", choices=("llm", "template"))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--input-price-per-million", type=float, default=0.4)
    parser.add_argument("--output-price-per-million", type=float, default=1.6)
    args = parser.parse_args()
    rows = [
        row
        for path in sorted((ROOT / "eval" / "golden").glob("*.json"))
        for row in inputs(path)
        if not args.case or any(row[0].startswith(selected) for selected in args.case)
    ]
    results = [
        run(case_id, inp, expected, args.writer) for case_id, inp, expected in rows
    ]
    report = {
        "writer": args.writer,
        "pricing_usd_per_million_tokens": {
            "input": args.input_price_per_million,
            "output": args.output_price_per_million,
        },
        "summary": summarize(
            results, args.input_price_per_million, args.output_price_per_million
        ),
        "cases": results,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
