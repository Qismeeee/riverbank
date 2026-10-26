import argparse
import csv
import json
import re
import sys
from abc import ABC, abstractmethod
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

import inject  # noqa: E402
import score  # noqa: E402
from memo_llm import inputs  # noqa: E402

import config  # noqa: E402
import jev  # noqa: E402
from llm import LlmClient, LlmRequest, LlmResponse, build_llm_client  # noqa: E402
from prompts import default_prompts  # noqa: E402
from services.copilot.advisor import CaseAdvisor, case_facts  # noqa: E402
from services.copilot.extractor import PlanExtractor  # noqa: E402
from services.copilot.models import AskInput, ExtractPlanInput  # noqa: E402
from services.guard import OutputGuard  # noqa: E402
from services.guardrail import GuardVerdict, InputGuard  # noqa: E402
from services.underwriting.memo import AUDIENCES, build_composer, default_validator  # noqa: E402
from services.underwriting.models import WorkflowError  # noqa: E402

GOLDEN = ROOT / "eval" / "golden"
EXTRACT_SET = GOLDEN / "copilot" / "extract.json"
JUDGE_PROMPT = ROOT / "knowledge" / "prompts" / "jev-eval-judge.md"
FIELDS = ("lot_value", "cash_on_hand", "sell_weeks", "requested_amount")
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

    def complete_json(self, request: LlmRequest) -> LlmResponse:
        self.last = None
        response = self.inner.complete_json(request)
        self.last = response.content
        return response


class AllowAll(InputGuard):
    def check(self, text: str) -> GuardVerdict:
        return GuardVerdict(False, 1.0)


def rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def golden_rows(only: list[str]) -> list[tuple[str, dict]]:
    return [
        row
        for path in sorted(GOLDEN.glob("*.json"))
        for row in inputs(path)
        if not only or any(row[0].startswith(c) for c in only)
    ]


def golden_case(case_id: str, inp: dict, writer: str):
    try:
        return score.golden_service(inp, build_composer(writer)).create(
            score.golden_plan(inp), case_id
        )
    except (ValidationError, WorkflowError):
        return None


class Section(ABC):
    name: str

    @abstractmethod
    def run(self) -> tuple[dict, list[dict]]: ...


class MemoSection(Section):
    name = "memo"

    def __init__(self, rows: list[tuple[str, dict]]):
        self.rows, self.validator = rows, default_validator()

    def run(self) -> tuple[dict, list[dict]]:
        labeled = [(i, golden_case(i, inp, "llm")) for i, inp in self.rows]
        labeled = [(i, c) for i, c in labeled if c and c.documents]
        cases = [c for _, c in labeled]
        errors, caught, injected, review = Counter(), Counter(), Counter(), []
        for label, case in labeled:
            for doc in case.documents:
                errors.update(e.split(":", 1)[-1] for e in doc["validation_errors"])
            final = case.documents[-1]
            for audience in AUDIENCES:
                for item in final[audience]:
                    review.append(self._row(label, case, final, audience, item))
                    if final["generator"] == "LLM":
                        self._inject(case, item, caught, injected)
        first = [c.documents[0] for c in cases]
        return {
            "cases": len(cases),
            "llm_first_attempt_ok": sum(d["generator"] == "LLM" and d["validated"] for d in first),
            "llm_final_ok": sum(c.documents[-1]["generator"] == "LLM" for c in cases),
            "template_fallback": sum(c.documents[-1]["generator"] != "LLM" for c in cases),
            "unvalidated": sum(not c.documents[-1]["validated"] for c in cases),
            "validator_errors": dict(errors.most_common()),
            "injection_catch_rate": {k: rate(caught[k], injected[k]) for k in sorted(injected)},
        }, review

    def _inject(self, case, item: dict, caught: Counter, injected: Counter) -> None:
        for kind, text in inject.mutations(item["text"]):
            injected[kind] += 1
            caught[kind] += bool(self.validator.sentence_errors(case, {**item, "text": text}))

    @staticmethod
    def _row(label: str, case, doc: dict, audience: str, item: dict) -> dict:
        return {
            "section": "memo",
            "case_id": label,
            "item": f"{audience}.{item['template']}",
            "status": case.status,
            "evidence": json.dumps(item["claims"], ensure_ascii=False),
            "text": item["text"],
            "source": doc["generator"],
            "codes": "",
        }


class ExtractSection(Section):
    name = "copilot_extract"

    def __init__(self, labeled: list[dict]):
        self.labeled = labeled
        prompt = default_prompts().get("copilot-extract")
        self.extractor = PlanExtractor(build_llm_client("copilot_extract"), prompt, AllowAll())

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
        sources, codes, review = Counter(), Counter(), []
        for case_id, inp in self.rows:
            case = golden_case(case_id, inp, "template")
            if case is None:
                continue
            for question in self.questions:
                advisor = CaseAdvisor(self.client, self.prompt, AllowAll(), self.output)
                turn = advisor.ask(case, AskInput(question=question))
                sources[turn.source] += 1
                codes.update(turn.guard_codes)
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

    def flags(self, row: dict) -> list[str]:
        state = [f"Trạng thái: {row['status']}", f"Bằng chứng: {row['evidence']}", row["text"]]
        answers = jev.decide(state, self.questions)
        return [n for n in self.questions if jev.noul_probability(answers, n) > self.threshold]

    def run(self, review: list[dict]) -> dict:
        flagged, errors = Counter(), 0
        for row in review:
            if row["source"].lower() != "llm" or row["section"] == "copilot_extract":
                continue
            try:
                row["judge"] = " ".join(self.flags(row))
            except Exception as e:
                row["judge"], errors = f"error:{type(e).__name__}", errors + 1
                continue
            flagged.update(row["judge"].split())
        judged = sum("judge" in r for r in review)
        return {"judged": judged, "errors": errors, "flags": dict(flagged.most_common())}


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", action="append", choices=["memo", "extract", "ask"])
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    chosen = args.section or ["memo", "extract", "ask"]
    rows = golden_rows(args.case)
    builders = {
        "memo": lambda: MemoSection(rows),
        "extract": lambda: ExtractSection(json.loads(EXTRACT_SET.read_text(encoding="utf-8"))),
        "ask": lambda: AskSection(rows),
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = {"run_at": stamp, "provider": config.LLM_PROVIDER, "model": config.LLM_MODEL}
    review: list[dict] = []
    for key in chosen:
        section = builders[key]()
        print(f"chạy {section.name} ...", flush=True)
        report[section.name], rows_out = section.run()
        review += rows_out
    if args.judge:
        print("chạy jev judge ...", flush=True)
        report["jev_judge"] = JevJudge().run(review)
    out = args.out or ROOT / "eval" / "out" / stamp
    write(out, report, review)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nreport: {out / 'report.json'}\nchấm tay: {out / 'review.csv'} (cột human_ok)")


if __name__ == "__main__":
    main()
