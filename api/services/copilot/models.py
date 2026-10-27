from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from services.underwriting.models import (
    FreeText,
    InputModel,
    Money,
    PartyId,
    Payment,
    PositiveMoney,
)

PlanField = Literal[
    "lot_value", "cash_on_hand", "supplier_schedule", "sell_weeks", "requested_amount"
]
PLAN_FIELDS: tuple[str, ...] = get_args(PlanField)


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftPayment(StrictOutput):
    week: int
    amount: int


class PlanFields(StrictOutput):
    lot_value: int | None
    cash_on_hand: int | None
    supplier_schedule: list[DraftPayment] | None
    sell_weeks: int | None
    requested_amount: int | None


class Extraction(PlanFields):
    missing_fields: list[PlanField]
    clarifying_question: str | None


class PlanDraft(PlanFields):
    seller_id: str
    shop_id: str


class FieldIssue(BaseModel):
    field: str
    code: str


class KnownPlan(InputModel):
    lot_value: PositiveMoney | None = None
    cash_on_hand: Money | None = None
    supplier_schedule: list[Payment] | None = None
    sell_weeks: Annotated[int, Field(gt=0)] | None = None
    requested_amount: Annotated[int, Field(strict=True, gt=0)] | None = None


class ExtractPlanInput(InputModel):
    seller_id: PartyId
    shop_id: PartyId
    message: Annotated[FreeText, Field(min_length=1)]
    known: KnownPlan | None = None


class ExtractPlanResponse(BaseModel):
    draft: PlanDraft
    ready: bool
    missing_fields: list[PlanField]
    issues: list[FieldIssue]
    clarifying_question: str | None
    reply: str
    sanitized_message: str
    prompt_version: str
    model: str


class AskInput(InputModel):
    question: Annotated[FreeText, Field(min_length=1)]


class Answer(StrictOutput):
    answer_vi: str
    figures_used: list[str]


class ChatTurn(BaseModel):
    turn_id: str
    question: str
    answer: str
    figures_used: list[str]
    source: Literal["llm", "template"]
    guard_codes: list[str]
    status: str
    prompt_version: str
    model: str | None
    asked_at: str


class AskResponse(ChatTurn):
    case_id: str


class ChatHistoryResponse(BaseModel):
    case_id: str
    turns: list[ChatTurn]
