from typing import Annotated

from fastapi import APIRouter, Query

from routers.auth import BankUser
from routers.jobs import Jobs
from routers.underwriting import Service
from services.studio import EvalReport, StudioPolicy, eval_report, policy_snapshot
from services.traffic import TrafficReport

router = APIRouter(prefix="/v1/studio", tags=["studio"])


@router.get("/queue-health")
def queue_health(jobs: Jobs, actor: BankUser) -> dict:
    return jobs.metrics()


@router.get("/policy", response_model=StudioPolicy)
def policy(service: Service, actor: BankUser) -> dict:
    return policy_snapshot(service)


@router.get("/eval-metrics", response_model=EvalReport)
def eval_metrics(actor: BankUser) -> dict:
    return eval_report()


@router.get("/traffic", response_model=TrafficReport)
def traffic(
    service: Service,
    actor: BankUser,
    window_from: Annotated[str | None, Query(alias="from")] = None,
    to: Annotated[str | None, Query()] = None,
    recent: Annotated[int, Query(ge=1, le=50)] = 12,
) -> dict:
    return service.traffic(window_from, to, recent)
