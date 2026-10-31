import json
from dataclasses import asdict
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, Header, Path, Query, Response, UploadFile

import config
from db.migrate import apply as migrate_database
from routers.auth import BankUser, Principal, Role, SellerUser, seller_of
from routers.limits import rate_limited
from services.statements.archive import (
    file_summary,
    latest_uploaded_statement,
    reading_payload,
    statement_period,
)
from services.statements.factory import build_document_store, build_statement_reader
from services.statements.reader import StatementConfig
from services.statements.storage import EXTENSIONS
from services.underwriting.cic import ProfileCicSource
from services.underwriting.evidence import read_evidence
from services.underwriting.gates import CicGate, JevCicGate, OpenCicGate
from services.underwriting.models import (
    Case,
    CaseDetail,
    CaseSummary,
    CicInquiry,
    DecisionInput,
    DecisionResponse,
    EvidenceResponse,
    HandoffResponse,
    Plan,
    QueueResponse,
    QueueStatusResponse,
    SellerView,
    Statement,
    StatementUpload,
    Status,
    VersionInput,
    WorkflowError,
)
from services.underwriting.pagination import decode_cursor, encode_cursor
from services.underwriting.repository import MemoryCaseRepository, PostgresCaseRepository
from services.underwriting.risk import BaselineRiskModel, LightGbmRiskModel, RiskModel
from services.underwriting.service import UnderwritingService, utc_now
from services.underwriting.sources import (
    CicSource,
    FixedCicSource,
    InternalLosAdapter,
    PostgresFeatureSource,
    seller_book_source,
)

router = APIRouter(prefix="/v1", tags=["underwriting"])

RISK_MODELS: dict[str, type[RiskModel]] = {
    "baseline": BaselineRiskModel,
    "lightgbm": LightGbmRiskModel,
}
CIC_GATES: dict[str, type[CicGate]] = {"open": OpenCicGate, "jev": JevCicGate}
CIC_SOURCES: dict[str, type[CicSource]] = {"fixed": FixedCicSource, "profiles": ProfileCicSource}


@lru_cache
def get_service() -> UnderwritingService:
    if config.WORKFLOW_MODE != "enabled":
        raise WorkflowError("WORKFLOW_DISABLED", 503)
    if config.WORKFLOW_STORAGE not in {"memory", "postgres"}:
        raise WorkflowError("INVALID_WORKFLOW_STORAGE", 503)
    if config.WORKFLOW_FEATURES not in {"seller_book", "postgres"}:
        raise WorkflowError("INVALID_WORKFLOW_FEATURES", 503)
    if config.WORKFLOW_RISK not in RISK_MODELS:
        raise WorkflowError("INVALID_WORKFLOW_RISK", 503)
    if config.WORKFLOW_CIC_GATE not in CIC_GATES:
        raise WorkflowError("INVALID_WORKFLOW_CIC_GATE", 503)
    if config.WORKFLOW_CIC_SOURCE not in CIC_SOURCES:
        raise WorkflowError("INVALID_WORKFLOW_CIC_SOURCE", 503)
    if config.WORKFLOW_CIC_GATE == "jev" and not config.OPENROUTER_API_KEY:
        raise WorkflowError("OPENROUTER_KEY_REQUIRED", 503)
    url = config.database_url()
    if "postgres" in {config.WORKFLOW_STORAGE, config.WORKFLOW_FEATURES} and not url:
        raise WorkflowError("DATABASE_REQUIRED", 503)
    if config.WORKFLOW_STORAGE == "postgres":
        migrate_database(url)
    repository = (
        PostgresCaseRepository(url)
        if config.WORKFLOW_STORAGE == "postgres"
        else MemoryCaseRepository()
    )
    features = (
        PostgresFeatureSource(url)
        if config.WORKFLOW_FEATURES == "postgres"
        else seller_book_source(utc_now())
    )
    return UnderwritingService(
        repository,
        features,
        RISK_MODELS[config.WORKFLOW_RISK](),
        CIC_SOURCES[config.WORKFLOW_CIC_SOURCE](),
        InternalLosAdapter(),
        cic_gate=CIC_GATES[config.WORKFLOW_CIC_GATE](),
        statements=build_statement_reader(),
        documents=build_document_store(config.DOCUMENT_STORE),
    )


Service = Annotated[UnderwritingService, Depends(get_service)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
Identifier = Annotated[str, Path(pattern=r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")]
PageLimit = Annotated[int | None, Query(ge=1, le=(1 << 63) - 2)]
PageOffset = Annotated[int | None, Query(ge=0, le=(1 << 63) - 1)]
PageCursor = Annotated[str | None, Query(max_length=2048)]


def summary(case: Case) -> dict:
    return {
        "case_id": case.case_id,
        "seller_id": case.seller_id,
        "shop_id": case.shop_id,
        "status": case.status,
        "version": case.version,
        "reason": case.reason,
        "created_at": case.created_at,
    }


def page_bounds(
    limit: int | None,
    page_size: int | None,
    offset: int | None,
    page_index: int | None,
    cursor: str | None,
) -> tuple[int, int]:
    if limit is not None and page_size is not None and limit != page_size:
        raise WorkflowError("INVALID_PAGINATION", 422)
    size = page_size if page_size is not None else limit if limit is not None else 50
    if page_index is not None:
        if offset is not None or cursor is not None:
            raise WorkflowError("INVALID_PAGINATION", 422)
        offset = page_index * size
    start = offset or 0
    if start > (1 << 63) - 1 or (cursor is not None and start):
        raise WorkflowError("INVALID_PAGINATION", 422)
    return size, start


def case_page(cases: list[Case], limit: int, offset: int, scope: str, cursor: str | None) -> dict:
    items = cases[:limit]
    has_more = len(cases) > limit
    page_index = offset // limit if cursor is None and offset % limit == 0 else None
    return {
        "items": [summary(case) for case in items],
        "limit": limit,
        "offset": offset,
        "page_size": limit,
        "page_index": page_index,
        "next_page_index": page_index + 1 if has_more and page_index is not None else None,
        "has_more": has_more,
        "next_cursor": encode_cursor(items[-1], scope) if has_more else None,
        "next_offset": offset + len(items) if has_more and cursor is None else None,
    }


@router.post(
    "/cases",
    status_code=201,
    response_model=CaseSummary,
    dependencies=[rate_limited("case_create")],
)
def create_case(plan: Plan, key: IdempotencyKey, actor: Principal, service: Service) -> dict:
    seller_of(actor, plan.seller_id)
    return summary(service.create(plan, key))


@router.get("/cases/{case_id}/seller-view", response_model=SellerView)
def seller_view(case_id: Identifier, actor: Principal, service: Service) -> dict:
    case = service.repository.get(case_id)
    seller_of(actor, case.seller_id)
    uploaded = latest_uploaded_statement(case)
    return {
        **summary(case),
        "explanation": (
            case.documents[-1]["seller_explanation"] if case.documents[-1]["validated"] else []
        ),
        "decision": (
            {
                "decision_type": case.decision["decision_type"],
                "final_amount": case.decision["final_amount"],
                "final_tenor_weeks": case.decision["final_tenor_weeks"],
            }
            if case.decision
            else None
        ),
        "statement_file": file_summary(uploaded) if uploaded else None,
        "statement_period": statement_period(case),
    }


@router.get("/queue", response_model=QueueStatusResponse)
def queue(
    service: Service,
    actor: BankUser,
    status: Status | Literal["DECIDED"] | None = None,
    limit: PageLimit = None,
    offset: PageOffset = None,
    cursor: PageCursor = None,
    page_size: PageLimit = None,
    page_index: PageOffset = None,
) -> dict:
    limit, offset = page_bounds(limit, page_size, offset, page_index, cursor)
    scope = f"queue:{status or 'open'}"
    after = decode_cursor(cursor, scope) if cursor is not None else None
    listing = service.repository.queue_page(status, limit + 1, offset, after)
    return {
        **case_page(listing.items, limit, offset, scope, cursor),
        "total": listing.total,
        "counts_by_status": listing.counts_by_status,
    }


@router.get("/me/cases", response_model=QueueResponse)
def my_cases(
    service: Service,
    actor: SellerUser,
    limit: PageLimit = None,
    offset: PageOffset = None,
    cursor: PageCursor = None,
    page_size: PageLimit = None,
    page_index: PageOffset = None,
) -> dict:
    limit, offset = page_bounds(limit, page_size, offset, page_index, cursor)
    scope = f"seller:{actor.subject}"
    after = decode_cursor(cursor, scope) if cursor is not None else None
    listing = service.repository.seller_page(actor.subject, limit + 1, offset, after)
    return {**case_page(listing.items, limit, offset, scope, cursor), "total": listing.total}


@router.get("/cases/{case_id}", response_model=CaseDetail)
def detail(case_id: Identifier, service: Service, actor: BankUser) -> dict:
    return asdict(service.repository.get(case_id))


@router.get("/cases/{case_id}/evidence/{evidence_id}", response_model=EvidenceResponse)
def evidence(
    case_id: Identifier, evidence_id: Identifier, service: Service, actor: BankUser
) -> dict:
    return read_evidence(service.repository.get(case_id), evidence_id)


@router.post("/cases/{case_id}/statements", response_model=CaseSummary)
def statement(case_id: Identifier, request: Statement, service: Service, actor: BankUser) -> dict:
    return summary(service.submit_statement(case_id, request, actor.subject))


class StatementUploadForm(StatementUpload):
    file: UploadFile


@router.post(
    "/cases/{case_id}/statements/upload",
    response_model=CaseSummary,
    dependencies=[rate_limited("statement_upload")],
)
def upload_statement(
    case_id: Identifier,
    form: Annotated[StatementUploadForm, Form(media_type="multipart/form-data")],
    service: Service,
    actor: Principal,
) -> dict:
    if actor.role == Role.SELLER:
        seller_of(actor, service.repository.get(case_id).seller_id)
    content = form.file.file.read(StatementConfig().max_bytes + 1)
    request = StatementUpload(**form.model_dump(exclude={"file"}))
    return summary(service.upload_statement(case_id, request, content, actor.subject))


def _readable_case(case_id: str, actor, service: UnderwritingService):
    case = service.repository.get(case_id)
    if actor.role == "underwriter":
        return case
    if actor.role == "seller":
        seller_of(actor, case.seller_id)
        return case
    raise WorkflowError("UNDERWRITER_REQUIRED", 403)


def _uploaded_statement(case) -> dict:
    statement = latest_uploaded_statement(case)
    if statement is None:
        raise WorkflowError("STATEMENT_NOT_FOUND", 404)
    return statement


def _download_name(case_id: str, suffix: str) -> dict[str, str]:
    name = f"HS-{case_id[:8].upper()}-{suffix}"
    return {"Content-Disposition": f'attachment; filename="{name}"'}


@router.get("/cases/{case_id}/statements/reading")
def statement_reading(case_id: Identifier, actor: Principal, service: Service) -> Response:
    statement = _uploaded_statement(_readable_case(case_id, actor, service))
    body = json.dumps(reading_payload(statement), ensure_ascii=False, indent=2).encode()
    return Response(
        content=body,
        media_type="application/json",
        headers=_download_name(case_id, "ket-qua-doc.json"),
    )


@router.get("/cases/{case_id}/statements/original")
def statement_original(case_id: Identifier, actor: Principal, service: Service) -> Response:
    statement = _uploaded_statement(_readable_case(case_id, actor, service))
    original = statement.get("original") or {}
    if original.get("store") != service.documents.name:
        raise WorkflowError("STATEMENT_FILE_NOT_STORED", 404)
    content, media = service.documents.get(original)
    extension = EXTENSIONS.get(media, "bin")
    return Response(
        content=content,
        media_type=media,
        headers=_download_name(case_id, f"sao-ke.{extension}"),
    )


@router.post("/cases/{case_id}/cic-inquiry", response_model=CaseSummary)
def cic_inquiry(
    case_id: Identifier, request: CicInquiry, service: Service, actor: BankUser
) -> dict:
    return summary(service.inquire_cic(case_id, request, actor.subject))


@router.post("/cases/{case_id}/decisions", response_model=DecisionResponse)
def decide(
    case_id: Identifier,
    request: DecisionInput,
    key: IdempotencyKey,
    service: Service,
    actor: BankUser,
) -> dict:
    case = service.decide(case_id, request, actor.subject, key)
    return {**summary(case), "decision": case.decision}


@router.post("/cases/{case_id}/handoff", response_model=HandoffResponse)
def handoff(case_id: Identifier, request: VersionInput, service: Service, actor: BankUser) -> dict:
    case = service.handoff(case_id, request.expected_version, actor.subject)
    return {**summary(case), "handoff": {k: v for k, v in case.handoff.items() if k != "payload"}}
