from datetime import timedelta

import pytest

from main import app
from routers import jobs as job_routes
from services.jobs import (
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    Job,
    JobConfig,
    JobHandler,
    JobWorker,
    MemoryJobQueue,
    utc_now,
)
from services.underwriting.models import WorkflowError
from services.underwriting.uploads import STATEMENT_UPLOAD, StatementUploadHandler
from tests.test_statements import csv_bytes, period_rows, reader, upload_service  # noqa: F401
from tests.test_underwriting import BANK, client, create, seller_headers, service  # noqa: F401

CFG = JobConfig(max_attempts=2, lease_seconds=60)


class Clock:
    def __init__(self):
        self.now = utc_now()

    def __call__(self):
        return self.now


class Scripted(JobHandler):
    kind = "scripted"

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    def handle(self, job, content):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def queue(client):  # noqa: F811
    jobs = MemoryJobQueue()
    app.dependency_overrides[job_routes.get_jobs] = lambda: jobs
    return jobs


def enqueue_upload(api, case, content, headers=BANK, version=None):
    start, end, _ = period_rows(case)
    return api.post(
        f"/v1/cases/{case.case_id}/statements/upload-jobs",
        headers={k: v for k, v in headers.items() if k != "Idempotency-Key"},
        data={
            "expected_version": str(version or case.version),
            "period_start": start,
            "period_end": end,
        },
        files={"file": ("saoke.csv", content, "application/octet-stream")},
    )


def statement_csv(case):
    _, _, (first_day, second_day) = period_rows(case)
    return csv_bytes(
        [
            [first_day, "SHOPEE VN THANH TOAN", "", "70.000.000"],
            [second_day, "SHOPEE VN THANH TOAN", "", "70.000.000"],
        ]
    )


def scripted_job(queue, kind="scripted"):
    return queue.enqueue(Job.new(kind, "c-1", "uw-1", {}, CFG), b"file")


def test_upload_job_runs_in_background_and_records_job_id(client, upload_service, queue):  # noqa: F811
    case = upload_service.repository.get(create(client, "ducthinh.tech")["case_id"])
    seller = seller_headers("ducthinh.tech")
    response = enqueue_upload(client, case, statement_csv(case), seller)
    assert response.status_code == 202, response.text
    job = response.json()
    assert response.headers["Location"] == f"/v1/jobs/{job['job_id']}"
    assert (job["status"], job["kind"], job["submitted_by"]) == (
        QUEUED,
        STATEMENT_UPLOAD,
        "ducthinh.tech",
    )
    assert upload_service.repository.get(case.case_id).version == case.version
    other = {k: v for k, v in seller_headers("minhchau.shop").items() if k != "Idempotency-Key"}
    assert client.get(f"/v1/jobs/{job['job_id']}", headers=other).status_code == 403
    worker = JobWorker(queue, [StatementUploadHandler(upload_service)], CFG)
    assert worker.run_once() and not worker.run_once()
    done = client.get(f"/v1/jobs/{job['job_id']}", headers=BANK).json()
    updated = upload_service.repository.get(case.case_id)
    assert done["status"] == SUCCEEDED and done["attempts"] == 1
    assert done["result"] == {
        "case_id": case.case_id,
        "status": updated.status,
        "version": updated.version,
    }
    assert updated.runs[-1]["statement"]["job_id"] == job["job_id"]
    assert queue._slots[job["job_id"]].content is None


def test_upload_job_prechecks_before_queueing(client, upload_service, queue):  # noqa: F811
    case = upload_service.repository.get(create(client, "ducthinh.tech")["case_id"])
    content = statement_csv(case)
    assert enqueue_upload(client, case, content, seller_headers("minhchau.shop")).status_code == 403
    assert enqueue_upload(client, case, content, version=case.version + 1).status_code == 409
    assert enqueue_upload(client, case, b"\x00\x81\xff").status_code == 415
    assert enqueue_upload(client, case, b"").status_code == 409
    assert queue._slots == {}


def test_unknown_job_is_not_found(client, queue):  # noqa: F811
    response = client.get("/v1/jobs/00000000-0000-0000-0000-000000000000", headers=BANK)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "JOB_NOT_FOUND"


def test_upload_handler_is_idempotent_on_retry(client, upload_service, queue):  # noqa: F811
    case = upload_service.repository.get(create(client, "ducthinh.tech")["case_id"])
    response = enqueue_upload(client, case, statement_csv(case))
    claim = queue.claim(60)
    handler = StatementUploadHandler(upload_service)
    first = handler.handle(claim.job, claim.content)
    second = handler.handle(claim.job, claim.content)
    assert first == second
    assert response.json()["job_id"] == claim.job.job_id
    assert len(upload_service.repository.get(case.case_id).runs) == 2


def test_transient_errors_retry_then_fail_and_client_errors_fail_at_once():
    queue = MemoryJobQueue()
    unavailable = WorkflowError("STATEMENT_OCR_UNAVAILABLE", 503)
    worker = JobWorker(queue, [Scripted(unavailable, RuntimeError("boom"))], CFG)
    job = scripted_job(queue)
    worker.run_once()
    retried = queue.get(job.job_id)
    assert (retried.status, retried.attempts) == (QUEUED, 1)
    assert retried.error["code"] == "STATEMENT_OCR_UNAVAILABLE"
    worker.run_once()
    failed = queue.get(job.job_id)
    assert (failed.status, failed.attempts) == (FAILED, 2)
    assert failed.error == {"code": "JOB_HANDLER_ERROR", "status": 500, "details": ["RuntimeError"]}
    worker = JobWorker(queue, [Scripted(WorkflowError("STATEMENT_UNREADABLE", 422))], CFG)
    job = scripted_job(queue)
    worker.run_once()
    assert (queue.get(job.job_id).status, queue.get(job.job_id).attempts) == (FAILED, 1)


def test_unknown_kind_fails_without_retry():
    queue = MemoryJobQueue()
    job = scripted_job(queue, "nope")
    assert JobWorker(queue, [], CFG).run_once()
    assert queue.get(job.job_id).error["code"] == "JOB_KIND_UNKNOWN"
    assert queue.get(job.job_id).status == FAILED


def test_expired_lease_is_reclaimed_then_failed_after_max_attempts():
    clock = Clock()
    queue = MemoryJobQueue(clock)
    job = scripted_job(queue)
    assert queue.claim(60).job.attempts == 1
    assert queue.claim(60) is None
    clock.now += timedelta(seconds=61)
    again = queue.claim(60)
    assert (again.job.job_id, again.job.status, again.job.attempts) == (job.job_id, RUNNING, 2)
    assert again.content == b"file"
    clock.now += timedelta(seconds=61)
    assert queue.claim(60) is None
    expired = queue.get(job.job_id)
    assert expired.status == FAILED and expired.error["code"] == "JOB_LEASE_EXPIRED"
    assert queue._slots[job.job_id].content is None


def test_worker_is_off_outside_prod(monkeypatch):
    monkeypatch.setattr(job_routes.config, "JOB_WORKER", "off")
    assert job_routes.start_worker() is None


def test_queue_health_is_bank_only_and_counts_backlog(client, queue):  # noqa: F811
    scripted_job(queue)
    response = client.get("/v1/studio/queue-health", headers=BANK)
    assert response.status_code == 200
    assert response.json()[QUEUED] == 1
    assert response.json()["oldest_queued_seconds"] >= 0
    assert client.get("/v1/studio/queue-health", headers=seller_headers()).status_code == 403
