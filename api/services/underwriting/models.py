from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Annotated[int, Field(strict=True, ge=0, le=10**15)]
PositiveMoney = Annotated[int, Field(strict=True, gt=0, le=10**15)]
PartyId = Annotated[str, Field(min_length=1, max_length=100)]
FreeText = Annotated[str, Field(max_length=2000)]


class Status(StrEnum):
    BLOCKED_INPUT = "BLOCKED_INPUT"
    ANALYZING = "ANALYZING"
    NO_LOAN_NEEDED = "NO_LOAN_NEEDED"
    AWAITING_STATEMENT = "AWAITING_STATEMENT"
    PENDING_REVIEW = "PENDING_REVIEW"
    ESCALATED = "ESCALATED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Payment(InputModel):
    week: Annotated[int, Field(strict=True, ge=0, le=52)]
    amount: PositiveMoney


class Plan(InputModel):
    seller_id: PartyId
    shop_id: PartyId
    lot_value: PositiveMoney
    cash_on_hand: Money
    supplier_schedule: Annotated[list[Payment], Field(min_length=1, max_length=100)]
    sell_weeks: Annotated[int, Field(strict=True, ge=1, le=26)]
    requested_amount: Annotated[int | None, Field(strict=True, gt=0, le=300_000_000)] = None
    note: FreeText = ""

    @model_validator(mode="after")
    def schedule_matches_lot(self) -> "Plan":
        if sum(p.amount for p in self.supplier_schedule) != self.lot_value:
            raise ValueError("supplier_schedule phải bằng lot_value")
        if any(p.week > self.sell_weeks for p in self.supplier_schedule):
            raise ValueError("lịch trả vượt kỳ bán")
        return self


class Statement(InputModel):
    expected_version: Annotated[int, Field(strict=True, ge=1)]
    period_start: str
    period_end: str
    marketplace_total: Money
    document_ref: Annotated[str, Field(min_length=1, max_length=500)]


class StatementUpload(InputModel):
    expected_version: Annotated[int, Field(ge=1)]
    period_start: str
    period_end: str


class DecisionInput(InputModel):
    expected_version: Annotated[int, Field(strict=True, ge=1)]
    decision_type: Literal["ACCEPT_PROPOSAL", "ACCEPT_COUNTER", "REJECT"]
    final_amount: PositiveMoney | None = None
    final_tenor_weeks: Annotated[int, Field(strict=True, ge=2, le=26)] | None = None
    verification_note: Annotated[str, Field(max_length=2000)] = ""
    reason: Annotated[str, Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def counter_requires_terms(self) -> "DecisionInput":
        counter = self.decision_type == "ACCEPT_COUNTER"
        if counter and (self.final_amount is None or self.final_tenor_weeks is None):
            raise ValueError("counter-offer cần số tiền và kỳ hạn")
        if not counter and (self.final_amount is not None or self.final_tenor_weeks is not None):
            raise ValueError("chỉ counter-offer được gửi điều kiện mới")
        return self


class VersionInput(InputModel):
    expected_version: Annotated[int, Field(strict=True, ge=1)]


class CicInquiry(VersionInput):
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class CaseSummary(BaseModel):
    case_id: str
    seller_id: str
    shop_id: str
    status: Status
    version: int
    reason: str | None
    created_at: str


class MemoSentence(BaseModel):
    template: str
    text: str
    claims: dict
    evidence_id: str


class EvidenceResponse(BaseModel):
    evidence_id: str
    case_id: str
    run_id: str
    code: str
    schema_version: str
    payload: dict
    payload_hash: str
    created_at: str


class DocumentResponse(BaseModel):
    document_id: str
    run_id: str
    attempt: int
    underwriter_memo: list[MemoSentence]
    seller_explanation: list[MemoSentence]
    validated: bool
    unfinished: bool
    validation_errors: list[str]
    generator: str
    prompt_version: str
    model: str | None = None
    created_at: str


class SellerDecision(BaseModel):
    decision_type: str
    final_amount: int | None
    final_tenor_weeks: int | None


class StatementPeriod(BaseModel):
    start: str
    end: str


class StatementFile(BaseModel):
    media_type: str | None = None
    pages: int | None = None
    parser: str | None = None
    lines: int | None = None
    stored: bool


class SellerView(CaseSummary):
    explanation: list[MemoSentence]
    decision: SellerDecision | None
    statement_file: StatementFile | None = None
    statement_period: StatementPeriod | None = None


class QueueResponse(BaseModel):
    items: list[CaseSummary]
    limit: int
    offset: int
    has_more: bool
    next_cursor: str | None
    next_offset: int | None


class CaseDetail(CaseSummary):
    plan: Plan
    runs: list[dict]
    evidence: list[EvidenceResponse]
    documents: list[DocumentResponse]
    events: list[dict]
    consent: dict
    decision: dict | None
    handoff: dict | None


class DecisionResponse(CaseSummary):
    decision: dict


class HandoffResponse(CaseSummary):
    handoff: dict


@dataclass
class Case:
    case_id: str
    seller_id: str
    shop_id: str
    plan: dict
    created_at: str
    status: str = "ANALYZING"
    version: int = 1
    reason: str | None = None
    runs: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    decision: dict | None = None
    handoff: dict | None = None
    consent: dict = field(default_factory=dict)


class WorkflowError(Exception):
    def __init__(
        self,
        code: str,
        status: int = 409,
        details: list | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.code = code
        self.status = status
        self.details = details or []
        self.headers = headers or {}
        super().__init__(code)
