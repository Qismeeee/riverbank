from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query

import config
from services.underwriting.evidence import read_evidence
from services.underwriting.gates import CicGate, JevCicGate, OpenCicGate
from services.underwriting.models import (
    Case,
    CaseDetail,
    CaseSummary,
    DecisionInput,
    DecisionResponse,
    EvidenceResponse,
    HandoffResponse,
    Plan,
    QueueResponse,
    SellerView,
    Statement,
    Status,
    VersionInput,
    WorkflowError,
)
from services.underwriting.repository import MemoryCaseRepository, PostgresCaseRepository
from services.underwriting.risk import DemoRiskModel, LightGbmRiskModel, RiskModel
from services.underwriting.service import UnderwritingService, utc_now
from services.underwriting.sources import (
    DemoCicSource,
    MockLosAdapter,
    PostgresFeatureSource,
    demo_source,
)

router = APIRouter(prefix="/v1", tags=["underwriting"])

RISK_MODELS: dict[str, type[RiskModel]] = {"demo": DemoRiskModel, "lightgbm": LightGbmRiskModel}
CIC_GATES: dict[str, type[CicGate]] = {"open": OpenCicGate, "jev": JevCicGate}


@dataclass(frozen=True)
class Actor:
    subject: str
    role: str


def get_actor(
    actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    actor_role: Annotated[str | None, Header(alias="X-Actor-Role")] = None,
) -> Actor:
    if config.WORKFLOW_MODE != "demo" or config.ENV != "local":
        raise WorkflowError("VERIFIED_IDENTITY_ADAPTER_REQUIRED", 503)
    if not actor_id or not actor_id.strip():
        raise WorkflowError("AUTHENTICATION_REQUIRED", 401)
    if actor_role not in {"seller", "underwriter"}:
        raise WorkflowError("ROLE_NOT_ALLOWED", 403)
    return Actor(actor_id, actor_role)


def underwriter(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
    if actor.role != "underwriter":
        raise WorkflowError("UNDERWRITER_REQUIRED", 403)
    return actor


@lru_cache
def get_service() -> UnderwritingService:
    if config.WORKFLOW_MODE != "demo" or config.ENV != "local":
        raise WorkflowError("WORKFLOW_DEMO_DISABLED", 503)
    if config.WORKFLOW_STORAGE not in {"memory", "postgres"}:
        raise WorkflowError("INVALID_WORKFLOW_STORAGE", 503)
    if config.WORKFLOW_FEATURES not in {"demo", "postgres"}:
        raise WorkflowError("INVALID_WORKFLOW_FEATURES", 503)
    if config.WORKFLOW_RISK not in RISK_MODELS:
        raise WorkflowError("INVALID_WORKFLOW_RISK", 503)
    if config.WORKFLOW_CIC_GATE not in CIC_GATES:
        raise WorkflowError("INVALID_WORKFLOW_CIC_GATE", 503)
    if config.WORKFLOW_CIC_GATE == "jev" and not config.OPENROUTER_API_KEY:
        raise WorkflowError("OPENROUTER_KEY_REQUIRED", 503)
    url = config.database_url()
    if "postgres" in {config.WORKFLOW_STORAGE, config.WORKFLOW_FEATURES} and not url:
        raise WorkflowError("DATABASE_REQUIRED", 503)
    repository = (
        PostgresCaseRepository(url)
        if config.WORKFLOW_STORAGE == "postgres"
        else MemoryCaseRepository()
    )
    features = (
        PostgresFeatureSource(url)
        if config.WORKFLOW_FEATURES == "postgres"
        else demo_source(utc_now())
    )
    return UnderwritingService(
        repository,
        features,
        RISK_MODELS[config.WORKFLOW_RISK](),
        DemoCicSource(),
        MockLosAdapter(),
        cic_gate=CIC_GATES[config.WORKFLOW_CIC_GATE](),
    )


Service = Annotated[UnderwritingService, Depends(get_service)]
BankUser = Annotated[Actor, Depends(underwriter)]
Principal = Annotated[Actor, Depends(get_actor)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
Identifier = Annotated[str, Path(pattern=r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")]


def summary(case: Case) -> dict:
    return {
        "case_id": case.case_id,
        "seller_id": case.seller_id,
        "shop_id": case.shop_id,
        "status": case.status,
        "version": case.version,
        "reason": case.reason,
        "created_at": case.created_at,
        "demo": True,
    }


@router.post("/cases", status_code=201, response_model=CaseSummary)
def create_case(plan: Plan, key: IdempotencyKey, actor: Principal, service: Service) -> dict:
    if actor.role != "seller" or actor.subject != plan.seller_id:
        raise WorkflowError("SELLER_SCOPE_MISMATCH", 403)
    return summary(service.create(plan, key))


@router.get("/cases/{case_id}/seller-view", response_model=SellerView)
def seller_view(case_id: Identifier, actor: Principal, service: Service) -> dict:
    case = service.repository.get(case_id)
    if actor.role != "seller" or case.seller_id != actor.subject:
        raise WorkflowError("SELLER_SCOPE_MISMATCH", 403)
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
    }


@router.get("/queue", response_model=QueueResponse)
def queue(
    service: Service,
    actor: BankUser,
    status: Status | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    return {
        "items": [summary(c) for c in service.repository.list_queue(status, limit, offset)],
        "limit": limit,
        "offset": offset,
    }


@router.get("/cases/{case_id}", response_model=CaseDetail)
def detail(case_id: Identifier, service: Service, actor: BankUser) -> dict:
    return {**asdict(service.repository.get(case_id)), "demo": True}


@router.get("/cases/{case_id}/evidence/{evidence_id}", response_model=EvidenceResponse)
def evidence(
    case_id: Identifier, evidence_id: Identifier, service: Service, actor: BankUser
) -> dict:
    return read_evidence(service.repository.get(case_id), evidence_id)


@router.post("/cases/{case_id}/statements", response_model=CaseSummary)
def statement(case_id: Identifier, request: Statement, service: Service, actor: BankUser) -> dict:
    return summary(service.submit_statement(case_id, request, actor.subject))


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
