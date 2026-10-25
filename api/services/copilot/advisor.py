from pydantic import ValidationError

from llm import LlmClient, LlmError, LlmRequest
from prompts import Prompt
from services.guard import OutputGuard, ints
from services.guardrail import InputGuard, PiiRedactor
from services.underwriting.models import Case

from .models import Answer, AskInput, AskResponse
from .replies import vnd
from .screening import screen

ANSWER_SCHEMA = Answer.model_json_schema()
MILLION = 1_000_000
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


def case_facts(case: Case) -> dict:
    run = case.runs[-1] if case.runs else {}
    cashflow = run.get("cashflow") or {}
    statement = run.get("statement")
    decision = case.decision or {}
    handoff = case.handoff or {}
    return {
        "status": case.status,
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
    ):
        self.llm, self.prompt, self.guard, self.output = llm, prompt, guard, output
        self.redactor = redactor or PiiRedactor()
        self.fallback = fallback or FactsReply()

    def ask(self, case: Case, request: AskInput) -> AskResponse:
        question = self.redactor.redact(request.question)
        screen(self.guard, question)
        facts = case_facts(case)
        try:
            answer, model = self._ask(question, request, facts)
        except (LlmError, ValidationError) as e:
            return self._template(case, facts, [type(e).__name__])
        codes = self._violations(answer, facts, case.status)
        if codes:
            return self._template(case, facts, codes)
        return AskResponse(
            case_id=case.case_id,
            status=case.status,
            answer=answer.answer_vi,
            figures_used=answer.figures_used,
            source="llm",
            guard_codes=[],
            prompt_version=self.prompt.version,
            model=model,
        )

    def _ask(self, question: str, request: AskInput, facts: dict) -> tuple[Answer, str]:
        history = [
            {"question": self.redactor.redact(t.question), "answer": self.redactor.redact(t.answer)}
            for t in request.history
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
        allowed |= {v // MILLION for v in allowed if v >= MILLION and v % MILLION == 0}
        return self.output.check(answer.answer_vi, allowed, status)

    def _template(self, case: Case, facts: dict, codes: list[str]) -> AskResponse:
        text, used = self.fallback.compose(facts)
        return AskResponse(
            case_id=case.case_id,
            status=case.status,
            answer=text,
            figures_used=used,
            source="template",
            guard_codes=codes,
            prompt_version=self.prompt.version,
            model=None,
        )
