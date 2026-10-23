from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from services.underwriting.models import FreeText, InputModel, PartyId

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


class ExtractPlanInput(InputModel):
    seller_id: PartyId
    shop_id: PartyId
    message: Annotated[FreeText, Field(min_length=1)]


class ExtractPlanResponse(BaseModel):
    draft: PlanDraft
    ready: bool
    missing_fields: list[PlanField]
    issues: list[FieldIssue]
    clarifying_question: str | None
    sanitized_message: str
    prompt_version: str
    model: str
