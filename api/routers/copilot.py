from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends

from llm import LlmError, build_llm_client
from prompts import default_prompts
from routers.auth import SellerUser, seller_of
from services.copilot.extractor import PlanExtractor
from services.copilot.models import ExtractPlanInput, ExtractPlanResponse
from services.guardrail import JevInputGuard
from services.underwriting.models import WorkflowError

router = APIRouter(prefix="/v1/copilot", tags=["copilot"])


@lru_cache
def get_extractor() -> PlanExtractor:
    try:
        llm = build_llm_client("copilot_extract")
    except LlmError as e:
        raise WorkflowError(e.code, 503) from e
    return PlanExtractor(llm, default_prompts().get("copilot-extract"), JevInputGuard())


Extractor = Annotated[PlanExtractor, Depends(get_extractor)]


@router.post("/extract-plan", response_model=ExtractPlanResponse)
def extract_plan(
    request: ExtractPlanInput, actor: SellerUser, extractor: Extractor
) -> ExtractPlanResponse:
    seller_of(actor, request.seller_id)
    return extractor.extract(request)
