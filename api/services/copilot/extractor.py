from pydantic import ValidationError

from llm import LlmClient, LlmError, LlmRequest
from prompts import Prompt
from services.guardrail import GuardUnavailable, InputGuard, PiiRedactor, jev_block_details
from services.underwriting.models import Plan, WorkflowError

from .grounding import PlanGrounder
from .models import (
    PLAN_FIELDS,
    Extraction,
    ExtractPlanInput,
    ExtractPlanResponse,
    FieldIssue,
    PlanDraft,
)

FIELD_LABELS = {
    "lot_value": "giá trị lô hàng",
    "cash_on_hand": "số vốn bạn đang có",
    "supplier_schedule": "lịch trả tiền cho xưởng theo tuần",
    "sell_weeks": "số tuần bán hết lô",
    "requested_amount": "số tiền muốn vay",
}
EXTRACTION_SCHEMA = Extraction.model_json_schema()


class PlanExtractor:
    def __init__(
        self,
        llm: LlmClient,
        prompt: Prompt,
        guard: InputGuard,
        redactor: PiiRedactor | None = None,
        grounder: PlanGrounder | None = None,
    ):
        self.llm, self.prompt, self.guard = llm, prompt, guard
        self.redactor = redactor or PiiRedactor()
        self.grounder = grounder or PlanGrounder()

    def extract(self, request: ExtractPlanInput) -> ExtractPlanResponse:
        sanitized = self.redactor.redact(request.message)
        self._screen(sanitized)
        extraction, model = self._ask(sanitized)
        draft = PlanDraft(
            seller_id=request.seller_id,
            shop_id=request.shop_id,
            **self.grounder.ground(extraction, sanitized),
        )
        missing = [name for name in PLAN_FIELDS if getattr(draft, name) is None]
        issues = [] if missing else self._issues(draft)
        return ExtractPlanResponse(
            draft=draft,
            ready=not missing and not issues,
            missing_fields=missing,
            issues=issues,
            clarifying_question=self._question(extraction, missing),
            sanitized_message=sanitized,
            prompt_version=self.prompt.version,
            model=model,
        )

    def _screen(self, text: str) -> None:
        try:
            verdict = self.guard.check(text)
        except GuardUnavailable as e:
            raise WorkflowError("GUARD_UNAVAILABLE", 503) from e
        if verdict.blocked:
            raise WorkflowError(
                "BLOCKED_INPUT", 422, jev_block_details(verdict.flags, verdict.threshold)
            )

    def _ask(self, text: str) -> tuple[Extraction, str]:
        request = LlmRequest(
            system=self.prompt.system,
            user={"message": text},
            schema=EXTRACTION_SCHEMA,
            schema_name="plan_extraction",
            temperature=self.prompt.temperature,
        )
        try:
            response = self.llm.complete_json(request)
            return Extraction.model_validate(response.content), response.model
        except LlmError as e:
            raise WorkflowError("EXTRACTION_UNAVAILABLE", 503, [e.code]) from e
        except ValidationError as e:
            raise WorkflowError("EXTRACTION_UNAVAILABLE", 503, ["LLM_SCHEMA_MISMATCH"]) from e

    @staticmethod
    def _issues(draft: PlanDraft) -> list[FieldIssue]:
        try:
            Plan.model_validate(draft.model_dump())
        except ValidationError as e:
            return [
                FieldIssue(field=str(err["loc"][0]) if err["loc"] else "plan", code=err["type"])
                for err in e.errors()
            ]
        return []

    @staticmethod
    def _question(extraction: Extraction, missing: list[str]) -> str | None:
        if not missing:
            return None
        if extraction.clarifying_question and set(extraction.missing_fields) == set(missing):
            return extraction.clarifying_question
        return f"Bạn cho mình biết thêm {', '.join(FIELD_LABELS[n] for n in missing)} nhé?"
