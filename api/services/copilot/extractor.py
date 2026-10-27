from pydantic import ValidationError

from llm import LlmClient, LlmError, LlmRequest
from prompts import Prompt
from services.guardrail import InputGuard, PiiRedactor
from services.underwriting.models import Plan, WorkflowError

from .grounding import PlanGrounder
from .models import (
    PLAN_FIELDS,
    Extraction,
    ExtractPlanInput,
    ExtractPlanResponse,
    FieldIssue,
    KnownPlan,
    PlanDraft,
)
from .replies import FIELD_LABELS, PlanReply
from .screening import screen

EXTRACTION_SCHEMA = Extraction.model_json_schema()


class PlanExtractor:
    def __init__(
        self,
        llm: LlmClient,
        prompt: Prompt,
        guard: InputGuard,
        redactor: PiiRedactor | None = None,
        grounder: PlanGrounder | None = None,
        replies: PlanReply | None = None,
    ):
        self.llm, self.prompt, self.guard = llm, prompt, guard
        self.redactor = redactor or PiiRedactor()
        self.grounder = grounder or PlanGrounder()
        self.replies = replies or PlanReply()

    def extract(self, request: ExtractPlanInput) -> ExtractPlanResponse:
        sanitized = self.redactor.redact(request.message)
        screen(self.guard, sanitized)
        extraction, model = self._ask(sanitized, request.known)
        draft = PlanDraft(
            seller_id=request.seller_id,
            shop_id=request.shop_id,
            **self.grounder.ground(extraction, sanitized, request.known),
        )
        missing = [name for name in PLAN_FIELDS if getattr(draft, name) is None]
        issues = [] if missing else self._issues(draft)
        question = self._question(extraction, missing)
        return ExtractPlanResponse(
            draft=draft,
            ready=not missing and not issues,
            missing_fields=missing,
            issues=issues,
            clarifying_question=question,
            reply=self.replies.compose(draft, question, issues),
            sanitized_message=sanitized,
            prompt_version=self.prompt.version,
            model=model,
        )

    def _ask(self, text: str, known: KnownPlan | None) -> tuple[Extraction, str]:
        request = LlmRequest(
            system=self.prompt.system,
            user={"message": text, "known": known.model_dump() if known else None},
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
