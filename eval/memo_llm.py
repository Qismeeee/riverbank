import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

import score  # noqa: E402

from services.underwriting.memo import build_composer  # noqa: E402
from services.underwriting.models import WorkflowError  # noqa: E402


def inputs(path: Path) -> list[tuple[str, dict]]:
    case = json.loads(path.read_text(encoding="utf-8"))
    if "variants" not in case:
        return [(case["case_id"], case["input"])]
    return [
        (f"{case['case_id']}-{v['variant_id']}", {**case.get("base_input", {}), **v["input"]})
        for v in case["variants"]
    ]


def run(case_id: str, inp: dict, writer: str) -> dict:
    service = score.golden_service(inp, build_composer(writer))
    try:
        case = service.create(score.golden_plan(inp), case_id)
    except (ValidationError, WorkflowError) as exc:
        return {"case_id": case_id, "skipped": type(exc).__name__}
    return {
        "case_id": case_id,
        "status": case.status,
        "attempts": [
            {
                "attempt": d["attempt"],
                "generator": d["generator"],
                "model": d["model"],
                "validated": d["validated"],
                "errors": d["validation_errors"],
            }
            for d in case.documents
        ],
        "underwriter_memo": [s["text"] for s in case.documents[-1]["underwriter_memo"]],
        "seller_explanation": [s["text"] for s in case.documents[-1]["seller_explanation"]],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--writer", default="llm")
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    rows = [
        row
        for path in sorted((ROOT / "eval" / "golden").glob("*.json"))
        for row in inputs(path)
        if not args.case or any(row[0].startswith(c) for c in args.case)
    ]
    results = [run(case_id, inp, args.writer) for case_id, inp in rows]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    done = [r for r in results if "attempts" in r]
    first = sum(
        r["attempts"][0]["validated"] and r["attempts"][0]["generator"] == "LLM" for r in done
    )
    print(f"LLM đạt ngay lần 1: {first}/{len(done)} case")


if __name__ == "__main__":
    main()
