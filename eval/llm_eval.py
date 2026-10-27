import argparse
import csv
import json
import math
import re
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

import qa_score  # noqa: E402
import score  # noqa: E402
from memo_gate import inputs  # noqa: E402

import config  # noqa: E402
import jev  # noqa: E402
from llm import LlmClient, LlmRequest, LlmResponse, build_llm_client  # noqa: E402
from prompts import default_prompts  # noqa: E402
from services.copilot.advisor import CaseAdvisor, case_facts  # noqa: E402
from services.copilot.extractor import PlanExtractor  # noqa: E402
from services.copilot.models import AskInput, ExtractPlanInput  # noqa: E402
from services.guard import OutputGuard  # noqa: E402
from services.guardrail import GuardVerdict, InputGuard  # noqa: E402
from services.metering import quote  # noqa: E402
from services.underwriting.models import Case, WorkflowError  # noqa: E402

GOLDEN = ROOT / "eval" / "golden"
EXTRACT_SET = GOLDEN / "copilot" / "extract.json"
JUDGE_PROMPT = ROOT / "knowledge" / "prompts" / "jev-eval-judge.md"
FIELDS = ("lot_value", "cash_on_hand", "sell_weeks", "requested_amount")
REQUIRED_JUDGE_FLAGS = frozenset(
    {
        "implies_fraud_accusation_indirectly",
        "contradicts_engine_outcome",
        "invents_fee_or_interest",
        "cites_number_absent_from_evidence",
        "assigns_fact_to_wrong_actor_or_unit",
        "fails_to_answer_question",
        "omits_required_answer_facts",
    }
)
QUESTIONS = (
    "Hồ sơ của tôi đang ở bước nào?",
    "Tôi được đề xuất vay bao nhiêu, trong bao lâu?",
    "Mỗi tuần bị trích bao nhiêu tiền payout?",
    "Vì sao hồ sơ chưa được duyệt?",
    "Lãi suất và phí là bao nhiêu?",
)


class RecordingClient(LlmClient):
    def __init__(self, inner: LlmClient):
        self.inner, self.model, self.last = inner, inner.model, None
        self.calls = []

    def complete_json(self, request: LlmRequest) -> LlmResponse:
        self.last = None
        started = time.perf_counter()
        try:
            response = self.inner.complete_json(request)
        except Exception as exc:
            self.calls.append(
                {
                    "duration_seconds": time.perf_counter() - started,
                    "error": type(exc).__name__,
                    "cost_micro_usd": None,
                }
            )
            raise
        self.calls.append(
            {
                "model": response.model,
                "cached": response.cached,
                "usage": response.usage,
                "input_hash": request.fingerprint(response.model),
                "duration_seconds": time.perf_counter() - started,
                "cost_micro_usd": 0
                if response.cached
                else quote(
                    response.model,
                    response.usage.get("prompt_tokens"),
                    response.usage.get("completion_tokens"),
                ),
            }
        )
        self.last = response.content
        return response


class AllowAll(InputGuard):
    def check(self, text: str) -> GuardVerdict:
        return GuardVerdict(False, 1.0)


def rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def golden_rows(only: list[str]) -> list[tuple[str, dict]]:
    return [
        (case_id, inp)
        for path in sorted(GOLDEN.glob("*.json"))
        for case_id, inp, _ in inputs(path)
        if not only or any(case_id.startswith(c) for c in only)
    ]


def golden_case(case_id: str, inp: dict) -> Case | None:
    _, case = score.execute_case(inp, case_id=case_id)
    return case


class Section(ABC):
    name: str

    @abstractmethod
    def run(self) -> tuple[dict, list[dict]]: ...


class ExtractSection(Section):
    name = "copilot_extract"

    def __init__(self, labeled: list[dict]):
        self.labeled = labeled
        prompt = default_prompts().get("copilot-extract")
        self.prompt = prompt
        self.client = RecordingClient(build_llm_client("copilot_extract"))
        self.extractor = PlanExtractor(self.client, prompt, AllowAll())

    def run(self) -> tuple[dict, list[dict]]:
        tally, review, failed = Counter(), [], []
        for row in self.labeled:
            try:
                draft = self.extractor.extract(
                    ExtractPlanInput(seller_id="eval", shop_id="eval", message=row["message"])
                ).draft
            except WorkflowError as e:
                failed.append({"id": row["id"], "code": e.code})
                continue
            got = {f: getattr(draft, f) for f in FIELDS}
            verdicts = {f: self._verdict(row["expected"][f], got[f]) for f in FIELDS}
            tally.update(verdicts.values())
            tally["messages_all_correct"] += all(v == "correct" for v in verdicts.values())
            review.append(
                {
                    "section": "copilot_extract",
                    "case_id": row["id"],
                    "item": "draft",
                    "status": "",
                    "evidence": json.dumps(row["expected"], ensure_ascii=False),
                    "text": f"{row['message']} => {json.dumps(got, ensure_ascii=False)}",
                    "source": "llm",
                    "codes": " ".join(f"{f}:{v}" for f, v in verdicts.items() if v != "correct"),
                }
            )
        filled = tally["correct"] + tally["wrong"] + tally["invented"]
        return {
            "messages": len(self.labeled),
            "unavailable": failed,
            "messages_all_correct": tally["messages_all_correct"],
            "fields": {k: tally[k] for k in ("correct", "missed", "wrong", "invented")},
            "precision_filled": rate(tally["correct"], filled),
            "invented_rate": rate(tally["invented"], filled),
        }, review

    @staticmethod
    def _verdict(expected, got) -> str:
        if expected == got:
            return "correct"
        if got is None:
            return "missed"
        return "invented" if expected is None else "wrong"


class AskSection(Section):
    name = "copilot_ask"

    def __init__(self, rows: list[tuple[str, dict]], questions: tuple[str, ...] = QUESTIONS):
        self.rows, self.questions = rows, questions
        self.client = RecordingClient(build_llm_client("copilot_ask"))
        self.prompt = default_prompts().get("copilot-qa")
        self.output = OutputGuard.from_guardrails(config.KNOWLEDGE_DIR / "guardrails.md")

    def run(self) -> tuple[dict, list[dict]]:
        sources, codes, review, failed, excluded = Counter(), Counter(), [], [], []
        labeled = qa_score.labels()
        quality_failures = []
        for case_id, inp in self.rows:
            try:
                case = golden_case(case_id, inp)
            except Exception as exc:
                failed.append({"case_id": case_id, "error": type(exc).__name__})
                continue
            if case is None:
                excluded.append(case_id)
                continue
            for question in self.questions:
                advisor = CaseAdvisor(self.client, self.prompt, AllowAll(), self.output)
                turn = advisor.ask(case, AskInput(question=question))
                sources[turn.source] += 1
                codes.update(turn.guard_codes)
                errors = qa_score.errors(labeled.get((case_id, question)), case.status, turn.answer)
                if errors:
                    quality_failures.append(
                        {"case_id": case_id, "question": question, "errors": errors}
                    )
                review.append(
                    {
                        "section": "copilot_ask",
                        "case_id": case_id,
                        "item": question,
                        "status": case.status,
                        "evidence": json.dumps(case_facts(case), ensure_ascii=False, default=str),
                        "text": turn.answer,
                        "source": turn.source,
                        "codes": " ".join(turn.guard_codes),
                        "rejected": self._rejected(turn),
                    }
                )
        total = sum(sources.values())
        return {
            "answers": total,
            "llm_answers": sources["llm"],
            "template_fallback_rate": rate(sources["template"], total),
            "guard_codes": dict(codes.most_common()),
            "expected_answers": (len(self.rows) - len(excluded)) * len(self.questions),
            "excluded_input_cases": excluded,
            "unavailable": failed,
            "quality_failures": quality_failures,
        }, review

    def _rejected(self, turn) -> str:
        if turn.source == "llm" or not self.client.last:
            return ""
        return json.dumps(self.client.last, ensure_ascii=False)


class JevJudge:
    def __init__(self, prompt: Path = JUDGE_PROMPT, threshold: float = 0.5):
        text = prompt.read_text(encoding="utf-8")
        block = text.split("```yaml", 1)[1].split("```", 1)[0]
        body = text.split("---", 2)[2]
        self.threshold = threshold
        self.questions = {
            name: jev.noul(f"{body}\n\nCâu hỏi: {name}", "YES", "NO")
            for name in re.findall(r"^(\w+):\s*$", block, re.MULTILINE)
        }
        if not REQUIRED_JUDGE_FLAGS <= self.questions.keys():
            raise ValueError("judge prompt is missing mandatory questions")

    def flags(self, row: dict) -> list[str]:
        state = [
            f"Câu hỏi: {row['item']}",
            f"Trạng thái: {row['status']}",
            f"Bằng chứng: {row['evidence']}",
            row["text"],
        ]
        answers = jev.decide(state, self.questions)
        return [n for n in self.questions if jev.noul_probability(answers, n) > self.threshold]

    def run(self, review: list[dict]) -> dict:
        flagged, errors = Counter(), 0
        for row in review:
            if row["section"] == "copilot_extract":
                continue
            try:
                row["judge"] = " ".join(self.flags(row))
            except Exception as e:
                row["judge"], errors = f"error:{type(e).__name__}", errors + 1
                continue
            flagged.update(row["judge"].split())
        attempted = sum("judge" in r for r in review)
        return {
            "attempted": attempted,
            "judged": attempted - errors,
            "errors": errors,
            "flags": dict(flagged.most_common()),
        }


def write(out: Path, report: dict, review: list[dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    columns = [
        "section",
        "case_id",
        "item",
        "status",
        "source",
        "codes",
        "judge",
        "text",
        "rejected",
    ]
    with (out / "review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, [*columns, "evidence", "human_ok"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows({**r, "human_ok": ""} for r in review)


def quality_errors(report: dict) -> list[str]:
    errors = []
    extract = report.get("copilot_extract")
    if extract is not None:
        if not extract["messages"] or extract["unavailable"]:
            errors.append("EXTRACTION_MISSING")
        if extract["messages_all_correct"] != extract["messages"]:
            errors.append("EXTRACTION_MISMATCH")
    ask = report.get("copilot_ask")
    if ask is not None:
        if not ask["answers"] or ask["answers"] != ask["expected_answers"] or ask["unavailable"]:
            errors.append("QA_MISSING")
        if (ask["template_fallback_rate"] or 0) > 0.2:
            errors.append("QA_FALLBACK_RATE")
        if ask.get("quality_failures"):
            errors.append("QA_CLAIM_MISMATCH")
        judge = report.get("jev_judge")
        if judge is None:
            errors.append("JUDGE_MISSING")
        elif judge["errors"] or judge["judged"] != ask["answers"] or judge["flags"]:
            errors.append("JUDGE_FAILED")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", action="append", choices=["extract", "ask"])
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    chosen = args.section or ["extract", "ask"]
    rows = golden_rows(args.case)
    builders = {
        "extract": lambda: ExtractSection(json.loads(EXTRACT_SET.read_text(encoding="utf-8"))),
        "ask": lambda: AskSection(rows),
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = {"run_at": stamp, "provider": config.LLM_PROVIDER, "model": config.LLM_MODEL}
    report["commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    report["working_tree_dirty"] = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    )
    report["source_hash"] = score.digest(
        {
            name: (ROOT / name).read_text(encoding="utf-8")
            for name in (
                "eval/llm_eval.py",
                "eval/score.py",
                "eval/qa_score.py",
                "api/services/copilot/advisor.py",
                "api/services/copilot/answer_guard.py",
            )
        }
    )
    report["dataset_hash"] = score.digest(
        {
            str(p.relative_to(GOLDEN)): p.read_text(encoding="utf-8")
            for p in sorted(GOLDEN.rglob("*.json"))
        }
    )
    report["judge_prompt_hash"] = score.digest(JUDGE_PROMPT.read_text(encoding="utf-8"))
    report["thresholds"] = {
        "extraction_exact_match": 1.0,
        "max_qa_fallback_rate": 0.2,
        "judge_errors": 0,
        "judge_flags": 0,
    }
    review: list[dict] = []
    for key in chosen:
        try:
            section = builders[key]()
            print(f"chạy {section.name} ...", flush=True)
            report[section.name], rows_out = section.run()
            calls = section.client.calls
            durations = sorted(call["duration_seconds"] for call in calls)
            report[section.name]["prompt_version"] = section.prompt.version
            report[section.name]["model_calls"] = calls
            report[section.name]["latency_seconds"] = {
                "p50": durations[max(0, math.ceil(len(durations) * 0.5) - 1)]
                if durations
                else None,
                "p95": durations[max(0, math.ceil(len(durations) * 0.95) - 1)]
                if durations
                else None,
            }
            report[section.name]["cost_micro_usd"] = (
                sum(call["cost_micro_usd"] for call in calls)
                if all(call["cost_micro_usd"] is not None for call in calls)
                else None
            )
            review += rows_out
        except Exception as exc:
            report.setdefault("execution_errors", []).append(
                {"section": key, "error": type(exc).__name__}
            )
    if args.judge:
        print("chạy jev judge ...", flush=True)
        try:
            report["jev_judge"] = JevJudge().run(review)
        except Exception as exc:
            report.setdefault("execution_errors", []).append(
                {"section": "judge", "error": type(exc).__name__}
            )
    errors = quality_errors(report)
    if report.get("execution_errors") or not rows:
        errors.append("EVALUATION_INCOMPLETE")
    report["quality_gate"] = {"passed": not errors, "errors": errors}
    out = args.out or ROOT / "eval" / "out" / stamp
    write(out, report, review)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nreport: {out / 'report.json'}\nchấm tay: {out / 'review.csv'} (cột human_ok)")
    return 0 if args.report_only or not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
