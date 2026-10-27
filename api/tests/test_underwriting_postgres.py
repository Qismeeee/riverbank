from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import config
from db.migrate import migrate
from db.seed_underwriting import seed
from services.copilot.history import PostgresChatHistory
from services.jobs import Job, JobConfig, PostgresJobQueue
from services.underwriting.models import DecisionInput, Plan, WorkflowError
from services.underwriting.repository import PostgresCaseRepository
from services.underwriting.risk import BaselineRiskModel
from services.underwriting.service import UnderwritingService
from services.underwriting.sources import (
    FixedCicSource,
    InternalLosAdapter,
    PostgresFeatureSource,
    seller_book_source,
)
from tests.test_underwriting import NOW, check_queue_scopes, plan

pytestmark = pytest.mark.skipif(
    not config.WORKFLOW_TEST_DATABASE_URL,
    reason="opt-in local PostgreSQL integration tests",
)


@pytest.fixture
def database():
    url = config.WORKFLOW_TEST_DATABASE_URL
    params = conninfo_to_dict(url)
    if params.get("host") not in {"127.0.0.1", "localhost"}:
        pytest.fail("integration database must be local")
    if params.get("dbname") != "riverbank_test":
        pytest.fail("integration database must be named riverbank_test")
    schema = "underwriting_test_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
    scoped = make_conninfo(url, options=f"-c search_path={schema}")
    try:
        with psycopg.connect(scoped) as conn:
            migrate(conn)
            migrate(conn)
        yield scoped
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


def make_service(database):
    return UnderwritingService(
        PostgresCaseRepository(database),
        seller_book_source(NOW),
        BaselineRiskModel(),
        FixedCicSource(),
        InternalLosAdapter(),
        clock=lambda: NOW,
    )


def test_postgres_restart_replay_and_append_only(database):
    service = make_service(database)
    created = service.create(Plan(**plan()), "create-1")
    restarted = make_service(database)
    assert restarted.repository.get(created.case_id) == created
    assert restarted.create(Plan(**plan()), "create-1") == created
    decision = DecisionInput(
        expected_version=1,
        decision_type="ACCEPT_COUNTER",
        final_amount=70_000_001,
        final_tenor_weeks=4,
        reason="test",
    )
    accepted = restarted.decide(created.case_id, decision, "uw", "decision-1")
    assert accepted.version == 2 and len(accepted.runs) == 2
    assert sum(accepted.decision["weekly_deductions"]) == 70_000_001
    sent = restarted.handoff(accepted.case_id, accepted.version, "uw")
    assert sent.handoff["status"] == "SUCCEEDED"
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="immutable handoff"):
            with conn.transaction():
                conn.execute(
                    "update handoffs set payload=jsonb_set(payload,'{payload}', '{}'::jsonb) "
                    "where case_id=%s",
                    (created.case_id,),
                )
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            with conn.transaction():
                conn.execute(
                    "update evidence set payload=payload where case_id=%s", (created.case_id,)
                )
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            with conn.transaction():
                conn.execute("delete from decisions where case_id=%s", (created.case_id,))


def test_postgres_lists_cases_by_seller(database):
    service = make_service(database)
    first = service.create(Plan(**plan()), "create-1")
    second = service.create(Plan(**plan(requested_amount=140_000_000)), "create-2")
    service.create(Plan(**plan("demo-a")), "create-3")
    mine = service.repository.list_by_seller("demo-b", 10, 0)
    assert {c.case_id for c in mine} == {first.case_id, second.case_id}
    assert service.repository.list_by_seller("demo-b", 1, 1) == mine[1:]


def test_postgres_rejects_cross_case_evidence_fk(database):
    service = make_service(database)
    one = service.create(Plan(**plan()), "one")
    two = service.create(Plan(**plan()), "two")
    from psycopg.types.json import Jsonb

    item = {**one.evidence[0], "evidence_id": str(uuid4()), "case_id": two.case_id, "code": "E99"}
    with psycopg.connect(database) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with conn.transaction():
                conn.execute(
                    "insert into evidence(id,case_id,ordinal,payload) values (%s,%s,%s,%s)",
                    (item["evidence_id"], two.case_id, 99, Jsonb(item)),
                )


def test_postgres_concurrent_decisions_and_atomic_rollback(database):
    service = make_service(database)
    case = service.create(Plan(**plan()), "create")
    bad = DecisionInput(
        expected_version=1,
        decision_type="ACCEPT_COUNTER",
        reason="test",
        final_amount=120_000_000,
        final_tenor_weeks=12,
    )
    with pytest.raises(WorkflowError, match="HARD_POLICY_FAILURE"):
        service.decide(case.case_id, bad, "uw", "invalid")
    assert service.repository.get(case.case_id) == case
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="test")

    def decide(key):
        try:
            make_service(database).decide(case.case_id, request, "uw", key)
            return "ok"
        except WorkflowError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(decide, ["one", "two"])) == ["conflict", "ok"]


def test_seller_book_seed_and_postgres_source(database):
    from services.underwriting.service import utc_now

    with psycopg.connect(database) as conn:
        seed(conn)
    source = PostgresFeatureSource(database)
    consent, features = source.fetch("demo-b", "demo-b", utc_now())
    assert consent["source"] == "seller_book"
    assert features["payout_origin"] == "estimated"
    with pytest.raises(WorkflowError, match="SCOPE"):
        source.fetch("demo-a", "demo-b", utc_now())


def test_etl_seed_preserves_source_and_labels_estimated_payout(database):
    from db.seed_underwriting import seed
    from etl.load import PostgresLoader
    from services.underwriting.service import utc_now
    from tests.test_etl import MANIFEST, TRANSFORMER, _run

    with psycopg.connect(database) as conn:
        loader = PostgresLoader(conn)
        loader.apply_schema()
        loader.load(MANIFEST, TRANSFORMER.transform(_run(), "2026-10-22"))
        seed(conn, "demo-etl", 984839978)
    consent, feature = PostgresFeatureSource(database).fetch("demo-etl", "984839978", utc_now())
    assert feature["source"] == "addlivetag" and feature["payout_origin"] == "estimated"
    assert feature["source_snapshot"]["gmv_30d"] == 100_620_000
    assert feature["weekly_payouts"][0]["amount"] == 100_620_000 * 7 * 85 // 3000
    assert feature["raw_refs"] and consent["source"] == "seller_book"
    with psycopg.connect(database) as conn:
        rows = conn.execute(
            "select payout_origin,payout_vnd from v_seller_payout_weekly where seller_id=%s",
            ("demo-etl",),
        ).fetchall()
        assert len(rows) == 12 and all(row[0] == "estimated" for row in rows)


def test_postgres_chat_history_keeps_order_and_is_append_only(database):
    case = make_service(database).create(Plan(**plan()), "chat-1")
    history = PostgresChatHistory(database)
    ids = [str(uuid4()) for _ in range(3)]
    for n, turn_id in enumerate(ids):
        history.append(case.case_id, {"turn_id": turn_id, "question": f"q{n}"})
    assert [t["turn_id"] for t in history.recent(case.case_id, 2)] == ids[1:]
    with psycopg.connect(database) as conn, pytest.raises(psycopg.Error):
        conn.execute("delete from copilot_turns")


def test_postgres_job_queue_claims_once_retries_and_expires(database):
    case = make_service(database).create(Plan(**plan()), "job-1")
    queue = PostgresJobQueue(database)
    cfg = JobConfig(max_attempts=2)
    jobs = [
        queue.enqueue(Job.new("k", case.case_id, "uw-1", {"n": n}, cfg), b"f%d" % n)
        for n in range(2)
    ]
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: queue.claim(60), range(2)))
    assert sorted(c.job.job_id for c in claims) == sorted(j.job_id for j in jobs)
    assert queue.claim(60) is None
    first, second = sorted(claims, key=lambda c: c.job.payload["n"])
    assert first.content == b"f0" and first.job.status == "RUNNING"
    queue.complete(first.job.job_id, {"ok": True})
    done = queue.get(first.job.job_id)
    assert (done.status, done.result) == ("SUCCEEDED", {"ok": True})
    queue.fail(second.job.job_id, {"code": "X", "status": 503}, True)
    assert queue.get(second.job.job_id).status == "QUEUED"
    again = queue.claim(0)
    assert (again.job.attempts, again.content) == (2, b"f1")
    assert queue.claim(60) is None
    failed = queue.get(second.job.job_id)
    assert failed.status == "FAILED" and failed.error["code"] == "JOB_LEASE_EXPIRED"
    with psycopg.connect(database) as conn:
        rows = conn.execute("select count(*) from jobs where content is not null").fetchone()
    assert rows[0] == 0


def test_postgres_queue_lists_decided_cases_newest_first(database):
    check_queue_scopes(make_service(database))


def test_postgres_outbox_queues_events_once_and_relays_one_at_a_time(database):
    service = make_service(database)
    case = service.create(Plan(**plan()), "create-1")
    repository = service.repository
    with repository.publishing(100) as batch:
        assert [m["event_id"] for m in batch] == [e["event_id"] for e in case.events]
        assert {m["case_id"] for m in batch} == {case.case_id}
        with PostgresCaseRepository(database).publishing(100) as other:
            assert other == []
    with repository.transaction(case.case_id, case.version) as locked:
        locked.events.append({**locked.events[-1], "event_id": str(uuid4()), "reason": "TEST"})
    with repository.publishing(100) as batch:
        assert [m["reason"] for m in batch] == ["TEST"]
    with repository.publishing(100) as batch:
        assert batch == []
