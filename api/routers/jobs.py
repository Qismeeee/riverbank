import logging
from functools import lru_cache
from threading import Event, Thread
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Response

import config
from routers.auth import Principal, seller_of
from routers.limits import rate_limited
from routers.underwriting import Identifier, Service, StatementUploadForm, get_service
from services.jobs import (
    Job,
    JobConfig,
    JobQueue,
    JobView,
    JobWorker,
    MemoryJobQueue,
    PostgresJobQueue,
)
from services.statements.reader import StatementConfig
from services.underwriting.models import StatementUpload
from services.underwriting.uploads import STATEMENT_UPLOAD, StatementUploadHandler

log = logging.getLogger("riverbank.jobs")
router = APIRouter(prefix="/v1", tags=["jobs"])


@lru_cache
def job_config() -> JobConfig:
    return JobConfig(max_attempts=config.JOB_MAX_ATTEMPTS, lease_seconds=config.JOB_LEASE_SECONDS)


@lru_cache
def get_jobs() -> JobQueue:
    if config.WORKFLOW_STORAGE == "postgres":
        return PostgresJobQueue(config.database_url())
    return MemoryJobQueue()


Jobs = Annotated[JobQueue, Depends(get_jobs)]


@router.post(
    "/cases/{case_id}/statements/upload-jobs",
    status_code=202,
    response_model=JobView,
    dependencies=[rate_limited("statement_upload")],
)
def upload_statement_job(
    case_id: Identifier,
    form: Annotated[StatementUploadForm, Form(media_type="multipart/form-data")],
    response: Response,
    service: Service,
    jobs: Jobs,
    actor: Principal,
) -> dict:
    case = service.repository.get(case_id)
    if actor.role == "seller":
        seller_of(actor, case.seller_id)
    content = form.file.file.read(StatementConfig().max_bytes + 1)
    request = StatementUpload(**form.model_dump(exclude={"file"}))
    service.check_upload(case_id, request, content)
    job = jobs.enqueue(
        Job.new(STATEMENT_UPLOAD, case_id, actor.subject, request.model_dump(), job_config()),
        content,
    )
    response.headers["Location"] = f"/v1/jobs/{job.job_id}"
    return job.view()


@router.get("/jobs/{job_id}", response_model=JobView)
def job_status(job_id: Identifier, service: Service, jobs: Jobs, actor: Principal) -> dict:
    job = jobs.get(job_id)
    if actor.role == "seller":
        seller_of(actor, service.repository.get(job.case_id).seller_id)
    return job.view()


def start_worker() -> Event | None:
    if config.JOB_WORKER != "on":
        return None
    stop = Event()

    def run() -> None:
        while not stop.is_set():
            try:
                worker = JobWorker(
                    get_jobs(), [StatementUploadHandler(get_service())], job_config()
                )
            except Exception:
                log.exception("job worker cannot start")
                stop.wait(30)
                continue
            worker.run(stop)

    Thread(target=run, name="riverbank-jobs", daemon=True).start()
    return stop
