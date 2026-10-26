from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query

import config
from llm import LlmError, build_llm_client
from prompts import default_prompts
from routers.auth import Principal, SellerUser, seller_of
from routers.underwriting import Identifier, Service
from services.copilot.advisor import CaseAdvisor
from services.copilot.extractor import PlanExtractor
from services.copilot.history import ChatHistory, MemoryChatHistory, PostgresChatHistory
from services.copilot.models import (
    AskInput,
    AskResponse,
    ChatHistoryResponse,
    ExtractPlanInput,
    ExtractPlanResponse,
)
from services.guard import OutputGuard
from services.guardrail import JevInputGuard
from services.underwriting.models import WorkflowError

router = APIRouter(prefix="/v1/copilot", tags=["copilot"])
cases_router = APIRouter(prefix="/v1/cases", tags=["copilot"])


def _llm(purpose: str):
    try:
        return build_llm_client(purpose)
    except LlmError as e:
        raise WorkflowError(e.code, 503) from e


@lru_cache
def get_extractor() -> PlanExtractor:
    return PlanExtractor(
        _llm("copilot_extract"), default_prompts().get("copilot-extract"), JevInputGuard()
    )


def _history() -> ChatHistory:
    if config.WORKFLOW_STORAGE == "postgres":
        return PostgresChatHistory(config.database_url())
    return MemoryChatHistory()


@lru_cache
def get_advisor() -> CaseAdvisor:
    return CaseAdvisor(
        _llm("copilot_ask"),
        default_prompts().get("copilot-qa"),
        JevInputGuard(),
        OutputGuard.from_guardrails(config.KNOWLEDGE_DIR / "guardrails.md"),
        history=_history(),
    )


Extractor = Annotated[PlanExtractor, Depends(get_extractor)]
Advisor = Annotated[CaseAdvisor, Depends(get_advisor)]


@router.post("/extract-plan", response_model=ExtractPlanResponse)
def extract_plan(
    request: ExtractPlanInput, actor: SellerUser, extractor: Extractor
) -> ExtractPlanResponse:
    seller_of(actor, request.seller_id)
    return extractor.extract(request)


@cases_router.post("/{case_id}/copilot/ask", response_model=AskResponse)
def ask_about_case(
    case_id: Identifier, request: AskInput, actor: SellerUser, advisor: Advisor, service: Service
) -> AskResponse:
    case = service.repository.get(case_id)
    seller_of(actor, case.seller_id)
    return advisor.ask(case, request)


@cases_router.get("/{case_id}/copilot/history", response_model=ChatHistoryResponse)
def chat_history(
    case_id: Identifier,
    actor: Principal,
    advisor: Advisor,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ChatHistoryResponse:
    case = service.repository.get(case_id)
    if actor.role != "underwriter":
        seller_of(actor, case.seller_id)
    return ChatHistoryResponse(case_id=case.case_id, turns=advisor.turns(case, limit))
