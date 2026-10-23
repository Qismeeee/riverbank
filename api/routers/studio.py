from fastapi import APIRouter

from routers.auth import BankUser
from routers.underwriting import Service
from services.studio import EvalReport, StudioPolicy, eval_report, policy_snapshot

router = APIRouter(prefix="/v1/studio", tags=["studio"])


@router.get("/policy", response_model=StudioPolicy)
def policy(service: Service, actor: BankUser) -> dict:
    return policy_snapshot(service)


@router.get("/eval-metrics", response_model=EvalReport)
def eval_metrics(actor: BankUser) -> dict:
    return eval_report()
