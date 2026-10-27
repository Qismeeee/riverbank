from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock

import pytest

from services.jobs import Job, JobConfig, MemoryJobQueue
from services.metering import record_usage
from services.traffic import workflow_metrics
from services.underwriting.memo import CodeMemoWriter
from services.underwriting.models import Plan, WorkflowError
from services.underwriting.service import UnderwritingService
from tests.test_jobs import Clock
from tests.test_memo import _service
from tests.test_underwriting import NOW, plan


def test_resume_after_cic_does_not_query_again_and_case_precedes_side_effect(monkeypatch):
    service = _service(CodeMemoWriter())
    original = service.cic.inquire
    calls = []

    def inquire(seller, now):
        assert service.repository.list_by_seller(seller, 100, 0)[0].status == "ANALYZING"
        calls.append(seller)
        record_usage("cic-test", "test", "test-model", {"prompt_tokens": 2}, False)
        return original(seller, now)

    monkeypatch.setattr(service.cic, "inquire", inquire)
    monkeypatch.setattr(service.memo, "compose", Mock(side_effect=RuntimeError("crash after CIC")))
    with pytest.raises(RuntimeError):
        service.create(Plan(**plan()), "resume")
    saved = service.repository.list_by_seller("minhchau.shop", 100, 0)[0]
    assert saved.status == "ANALYZING" and not saved.runs
    assert saved.progress["steps"]["CIC_INQUIRY"]["state"] == "SUCCEEDED"
    restarted = UnderwritingService(
        service.repository,
        service.features,
        service.risk,
        service.cic,
        service.los,
        clock=lambda: NOW,
    )
    case = restarted.create(Plan(**plan()), "resume")
    assert case.status == "PENDING_REVIEW" and case.version == 1
    assert len(case.runs) == 1 and calls == ["minhchau.shop"]
    assert len(case.runs[0]["model_calls"]) == 1
    assert workflow_metrics([case], NOW)["recovered_initial_assessments"] == 1
    assert all(
        s["cached"]
        for s in case.runs[0]["steps"]
        if s["step"] in {"CASHFLOW", "RISK", "CIC_INQUIRY"}
    )


def test_unknown_external_cic_outcome_requires_reconciliation(monkeypatch):
    service = _service(CodeMemoWriter())
    service.cic.replay_safe = False
    inquiry = Mock(side_effect=ConnectionError("response lost after external effect"))
    monkeypatch.setattr(service.cic, "inquire", inquiry)
    with pytest.raises(ConnectionError):
        service.create(Plan(**plan()), "unknown")
    with pytest.raises(WorkflowError, match="CIC_RECONCILIATION_REQUIRED"):
        service.create(Plan(**plan()), "unknown")
    assert inquiry.call_count == 1


def test_resume_rejects_changed_engine_configuration(monkeypatch):
    from services.cashflow import CashFlowConfig

    service = _service(CodeMemoWriter())
    monkeypatch.setattr(service.memo, "compose", Mock(side_effect=RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        service.create(Plan(**plan()), "changed-config")
    service.engine.cfg = CashFlowConfig(buffer_factor=1.12)
    with pytest.raises(WorkflowError, match="ASSESSMENT_VERSION_CHANGED"):
        service.create(Plan(**plan()), "changed-config")


def test_recovery_does_not_bypass_revoked_consent(monkeypatch):
    service = _service(CodeMemoWriter())
    monkeypatch.setattr(service.memo, "compose", Mock(side_effect=RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        service.create(Plan(**plan()), "revoked")
    service.features.records[("minhchau.shop", "minhchau.shop")][0]["revoked_at"] = NOW.isoformat()
    assert service.resume_pending() == {"resumed": 0, "blocked": 1}
    assert service.repository.list_by_seller("minhchau.shop", 100, 0)[0].status == "ANALYZING"


def test_concurrent_create_has_one_assessment_and_one_external_call(monkeypatch):
    service = _service(CodeMemoWriter())
    inquire = Mock(wraps=service.cic.inquire)
    monkeypatch.setattr(service.cic, "inquire", inquire)
    with ThreadPoolExecutor(2) as pool:
        cases = list(pool.map(lambda _: service.create(Plan(**plan()), "concurrent"), range(2)))
    assert cases[0] == cases[1] and inquire.call_count == 1


def test_expired_worker_cannot_complete_fail_or_commit_business_state():
    clock = Clock()
    queue = MemoryJobQueue(clock)
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "lease")
    job = queue.enqueue(Job.new("test", case.case_id, "uw", {}, JobConfig()), b"file")
    first = queue.claim(1)
    clock.now += timedelta(seconds=2)
    second = queue.claim(60)
    assert not queue.complete(job.job_id, {"writer": "old"}, first.job.attempts)
    assert not queue.fail(job.job_id, {"code": "old"}, False, first.job.attempts)
    with pytest.raises(WorkflowError, match="JOB_LEASE_LOST"):
        with service.repository.transaction(
            case.case_id, case.version, lambda conn: queue.committing(first.job, conn)
        ) as locked:
            locked.reason = "stale business change"
    assert service.repository.get(case.case_id) == case
    assert queue.complete(job.job_id, {"writer": "new"}, second.job.attempts)
    assert not queue.complete(job.job_id, {"writer": "old"}, first.job.attempts)
    assert queue.get(job.job_id).result == {"writer": "new"}


def test_lease_expiry_during_business_work_rolls_back():
    clock = Clock()
    queue = MemoryJobQueue(clock)
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "expires-during-work")
    queue.enqueue(Job.new("test", case.case_id, "uw", {}, JobConfig()), b"file")
    claim = queue.claim(1)
    with pytest.raises(WorkflowError, match="JOB_LEASE_LOST"):
        with service.repository.transaction(
            case.case_id, case.version, lambda conn: queue.committing(claim.job, conn)
        ) as locked:
            locked.reason = "must not commit"
            clock.now += timedelta(seconds=2)
    assert service.repository.get(case.case_id) == case
