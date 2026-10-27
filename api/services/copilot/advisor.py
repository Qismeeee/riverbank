from datetime import UTC, date, datetime, timedelta
from typing import Callable
from uuid import uuid4

from pydantic import ValidationError

from llm import LlmClient, LlmError, LlmRequest
from prompts import Prompt
from services.guard import OutputGuard, expand_units, ints
from services.guardrail import InputGuard, PiiRedactor
from services.underwriting.models import Case

from .history import ChatHistory, MemoryChatHistory
from .models import Answer, AskInput, AskResponse, ChatTurn
from .replies import vnd
from .screening import screen

ANSWER_SCHEMA = Answer.model_json_schema()
HISTORY_TURNS = 6
STATEMENT_STATUSES = {"AWAITING_STATEMENT", "ESCALATED"}
STATEMENT_STEP = (
    "Bạn nộp sao kê tài khoản nhận payout, kỳ sao kê bắt đầu và kết thúc vào thứ Hai, "
    "nằm trong khoảng {start} đến {end}."
)
STATUS_TEXT = {
    "ANALYZING": "Hồ sơ đang được hệ thống tính.",
    "BLOCKED_INPUT": "Hồ sơ chưa tính được vì nội dung gửi lên chưa hợp lệ.",
    "NO_LOAN_NEEDED": "Theo kế hoạch bạn gửi, dòng tiền không bị âm nên chưa cần vay.",
    "AWAITING_STATEMENT": "Ngân hàng cần thêm sao kê tài khoản để đối chiếu tiền sàn.",
    "PENDING_REVIEW": "Hồ sơ đang chờ ngân hàng xem.",
    "ESCALATED": "Hồ sơ đang chờ ngân hàng xem kỹ hơn.",
    "ACCEPTED": "Ngân hàng đã chấp nhận hồ sơ.",
    "REJECTED": "Ngân hàng đã từ chối hồ sơ.",
}


def _vn_date(day: date) -> str:
    return day.strftime("%d/%m/%Y")


def _explanation(case: Case) -> list[str]:
    document = case.documents[-1] if case.documents else {}
    if not document.get("validated"):
        return []
    return [item["text"] for item in document.get("seller_explanation") or []]


def _statement_window(case: Case, run: dict) -> dict | None:
    results = (run.get("policy") or {}).get("results") or []
    r05_failed = any(r["rule_id"] == "R05" and not r["passed"] for r in results)
    if case.status not in STATEMENT_STATUSES or not r05_failed:
        return None
    weeks = [
        date.fromisoformat(w["week_start"])
        for w in run["input_snapshot"]["features"]["weekly_payouts"]
    ]
    return {"start": _vn_date(min(weeks)), "end": _vn_date(max(weeks) + timedelta(days=7))}


def case_facts(case: Case) -> dict:
    run = case.runs[-1] if case.runs else {}
    cashflow = run.get("cashflow") or {}
    statement = run.get("statement")
    decision = case.decision or {}
    handoff = case.handoff or {}
    return {
        "status": case.status,
        "reason": case.reason,
        "explanation": _explanation(case),
        "statement_window": _statement_window(case, run) if run else None,
        "lot_value": case.plan.get("lot_value"),
        "cash_on_hand": case.plan.get("cash_on_hand"),
        "sell_weeks": case.plan.get("sell_weeks"),
        "requested_amount": case.plan.get("requested_amount"),
        "gap": cashflow.get("gap"),
        "amount_proposed": cashflow.get("amount_proposed"),
        "tenor_weeks": cashflow.get("tenor_weeks"),
        "weekly_deduction": max(cashflow.get("weekly_deductions") or [], default=None),
        "statement_matched": statement["matched"] if statement else None,
        "final_amount": decision.get("final_amount"),
        "final_tenor_weeks": decision.get("final_tenor_weeks"),
        "los_reference": handoff.get("los_reference"),
        "bank_tariff": None,
    }


class FactsReply:
    def compose(self, facts: dict) -> tuple[str, list[str]]:
        parts, used = [STATUS_TEXT[facts["status"]]], ["status"]
        amount, tenor = self._terms(facts)
        if amount and tenor:
            parts.append(
                f"Số tiền {vnd(facts[amount])} trong {facts[tenor]} tuần, "
                f"mỗi tuần trừ {vnd(facts['weekly_deduction'])} từ payout."
            )
            used += [amount, tenor, "weekly_deduction"]
        if window := facts["statement_window"]:
            parts.append(STATEMENT_STEP.format(**window))
            used.append("statement_window")
        if facts["los_reference"]:
            parts.append(f"Mã tham chiếu giải ngân {facts['los_reference']}.")
            used.append("los_reference")
        return " ".join(parts), used

    @staticmethod
    def _terms(facts: dict) -> tuple[str | None, str | None]:
        if facts["final_amount"] and facts["final_tenor_weeks"]:
            return "final_amount", "final_tenor_weeks"
        if facts["amount_proposed"] and facts["tenor_weeks"] and facts["weekly_deduction"]:
            return "amount_proposed", "tenor_weeks"
        return None, None


class CaseAdvisor:
    def __init__(
        self,
        llm: LlmClient,
        prompt: Prompt,
        guard: InputGuard,
        output: OutputGuard,
        redactor: PiiRedactor | None = None,
        fallback: FactsReply | None = None,
        history: ChatHistory | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.llm, self.prompt, self.guard, self.output = llm, prompt, guard, output
        self.redactor = redactor or PiiRedactor()
        self.fallback = fallback or FactsReply()
        self.history = history or MemoryChatHistory()
        self.clock = clock

    def ask(self, case: Case, request: AskInput) -> AskResponse:
        question = self.redactor.redact(request.question)
        screen(self.guard, question)
        facts = case_facts(case)
        try:
            answer, model = self._ask(question, case.case_id, facts)
            codes = self._violations(answer, facts, case.status)
        except (LlmError, ValidationError) as e:
            answer, model, codes = None, None, [type(e).__name__]
        if codes:
            text, used = self.fallback.compose(facts)
            turn = self._turn(case, question, text, used, "template", codes, None)
        else:
            turn = self._turn(
                case, question, answer.answer_vi, answer.figures_used, "llm", [], model
            )
        self.history.append(case.case_id, turn.model_dump())
        return AskResponse(case_id=case.case_id, **turn.model_dump())

    def turns(self, case: Case, limit: int) -> list[ChatTurn]:
        return [ChatTurn(**t) for t in self.history.recent(case.case_id, limit)]

    def _ask(self, question: str, case_id: str, facts: dict) -> tuple[Answer, str]:
        history = [
            {"question": t["question"], "answer": t["answer"]}
            for t in self.history.recent(case_id, HISTORY_TURNS)
        ]
        response = self.llm.complete_json(
            LlmRequest(
                system=self.prompt.system,
                user={"case": facts, "history": history, "question": question},
                schema=ANSWER_SCHEMA,
                schema_name="case_answer",
                temperature=self.prompt.temperature,
            )
        )
        return Answer.model_validate(response.content), response.model

    def _violations(self, answer: Answer, facts: dict, status: str) -> list[str]:
        unknown = [f for f in answer.figures_used if facts.get(f) is None]
        if unknown:
            return ["unknown_figure"]
        allowed = ints([facts[f] for f in answer.figures_used])
        return self.output.check(expand_units(answer.answer_vi), allowed, status)

    def _turn(
        self,
        case: Case,
        question: str,
        answer: str,
        used: list[str],
        source: str,
        codes: list[str],
        model: str | None,
    ) -> ChatTurn:
        return ChatTurn(
            turn_id=str(uuid4()),
            question=question,
            answer=answer,
            figures_used=used,
            source=source,
            guard_codes=codes,
            status=case.status,
            prompt_version=self.prompt.version,
            model=model,
            asked_at=self.clock().isoformat(),
        )
