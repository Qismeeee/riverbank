import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

import jev
from llm import LlmClient, LlmRequest, LlmResponse
from services.metering import (
    bind_run,
    load_prices,
    open_scope,
    quote,
    record_usage,
    reset_meter,
    token_count,
)
from services.statements.classifiers import JevPayoutClassifier, PatternPayoutClassifier
from services.statements.vision import VisionStatementParser
from services.traffic import build_traffic, percentile
from services.underwriting.gates import JevCicGate
from services.underwriting.models import Case, Plan, WorkflowError
from services.underwriting.repository import MemoryCaseRepository
from services.underwriting.risk import BaselineRiskModel
from services.underwriting.service import UnderwritingService
from services.underwriting.sources import FixedCicSource, InternalLosAdapter, seller_book_source
from tests.test_underwriting import (  # noqa: F401
    BANK,
    NOW,
    client,
    create,
    plan,
    seller_headers,
    service,
)

CLOCK = datetime(2026, 10, 22, 3, tzinfo=UTC)
PHONE = "0901234567"


def _service(repo: MemoryCaseRepository | None = None, **kwargs) -> UnderwritingService:
    return UnderwritingService(
        repo or MemoryCaseRepository(),
        seller_book_source(NOW),
        BaselineRiskModel(),
        FixedCicSource(),
        InternalLosAdapter(),
        clock=lambda: NOW,
        **kwargs,
    )


def _case(case_id: str, created: str, status: str, runs: list, documents=None, reason=None):
    case = Case(case_id, "demo-b", "demo-b", {"note": PHONE}, created, status=status, reason=reason)
    case.runs = runs
    case.documents = documents or [{"underwriter_memo": [{"text": "MEMO_SECRET_TEXT"}]}]
    return case


def _metered(run_id: str, trigger: str, run_no: int, duration: int, calls=None) -> dict:
    return {
        "run_id": run_id,
        "trigger": trigger,
        "run_no": run_no,
        "started_at": "2026-10-22T00:00:00+00:00",
        "finished_at": "2026-10-22T00:00:01+00:00",
        "duration_ms": duration,
        "model_calls": calls or [],
    }


def _call(model: str, cost: int | None) -> dict:
    return {
        "model": model,
        "purpose": "statement_ocr",
        "provider": "openai",
        "cost_micro_usd": cost,
        "prompt_tokens": None if cost is None else 1_000_000,
        "completion_tokens": 0,
        "total_tokens": None,
        "cached": False,
    }


def test_percentile_uses_the_ceiling_rank():
    assert percentile([], 50) is None
    assert percentile([4, 8], 50) == 4
    assert percentile([4, 8], 95) == 8
    assert percentile([10, 20, 30], 50) == 20
    assert percentile([10, 20, 30], 95) == 30


def test_latency_breaches_include_recovery_time_and_support_legacy_runs():
    resumed = _metered("resumed", "INITIAL", 1, 1000)
    resumed["workflow_elapsed_ms"] = 120_000
    legacy = _metered("legacy", "INITIAL", 1, 100_000)
    cases = [
        _case("resumed", CLOCK.isoformat(), "PENDING_REVIEW", [resumed]),
        _case("legacy", CLOCK.isoformat(), "PENDING_REVIEW", [legacy]),
    ]
    report = build_traffic(cases, CLOCK, 2)
    assert report["summary"]["workflow"]["latency_target_breaches"] == 2


def test_price_file_quotes_tokens_and_ignores_text_length():
    assert load_prices()["gpt-4.1-mini"] == (400_000, 1_600_000)
    assert quote("gpt-4.1-mini", 1_000_000, 0) == 400_000
    assert quote("gpt-4.1-mini", 2, 0) == 1
    assert quote("gpt-4.1-mini", None, 10) is None
    assert quote("typesafe/jev-1.14", 10, 10) is None
    usage = {"characters": 50_000, "total_tokens": 99}
    assert token_count(usage, "prompt_tokens") is None
    assert token_count(usage, "total_tokens") == 99


def test_record_usage_stores_one_row_and_skips_callers_outside_a_run():
    record_usage("memo_writer", "openai", "gpt-4.1-mini", {"prompt_tokens": 1}, False)
    token = open_scope("case-1", lambda: CLOCK)
    try:
        _, bucket = bind_run("case-1", "run-1", lambda: CLOCK)
        record_usage(
            "statement_ocr",
            "openai",
            "gpt-4.1-mini-2025-04-14",
            {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2, "characters": 9_000},
            True,
        )
        calls = bucket.snapshot("run-1")
    finally:
        reset_meter(token)
    (call,) = calls
    assert call["purpose"] == "statement_ocr"
    assert call["provider"] == "openai"
    assert call["cached"] is True
    assert call["prompt_tokens"] == 2 and call["cost_micro_usd"] == 1
    assert "characters" not in call
    assert call["case_id"] == "case-1" and call["run_id"] == "run-1"


def test_pattern_classification_has_no_row_and_each_jev_batch_does():
    seen = []

    def decide(state, questions, timeout):
        seen.append(questions)
        jev.note_usage("typesafe/jev-1.14", {"prompt_tokens": 4, "completion_tokens": 1})
        return {name: {"noul": 0.2} for name in questions}

    token = open_scope("case-1", lambda: CLOCK)
    try:
        PatternPayoutClassifier("shopee").classify(["Shopee payout"])
        JevPayoutClassifier(batch_size=1, decide=decide).classify(["a", "b"])
        _, bucket = bind_run("case-1", "run-1", lambda: CLOCK)
        calls = bucket.snapshot("run-1")
    finally:
        reset_meter(token)
    assert len(seen) == 2
    assert [call["purpose"] for call in calls] == ["statement_classifier", "statement_classifier"]
    assert calls[0]["prompt_tokens"] == 4 and calls[0]["cost_micro_usd"] is None


def test_ocr_pages_in_worker_threads_stay_on_the_run(monkeypatch):
    class Client(LlmClient):
        model = "gpt-4.1-mini"
        provider = "openai"

        def complete_json(self, request: LlmRequest) -> LlmResponse:
            return LlmResponse(
                {
                    "unreadable": False,
                    "opening_balance_vnd": None,
                    "closing_balance_vnd": None,
                    "lines": [],
                },
                "gpt-4.1-mini",
                {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            )

    monkeypatch.setattr("services.statements.vision.render_pages", lambda *args: {1: "u1", 2: "u2"})
    parser = VisionStatementParser(
        Client(), SimpleNamespace(system="s", temperature=0, version="v")
    )
    token = open_scope("case-1", lambda: CLOCK)
    try:
        _, bucket = bind_run("case-1", "run-1", lambda: CLOCK)
        parser._read(SimpleNamespace(), [1, 2], 2, 1.0, False)
        calls = bucket.snapshot("run-1")
    finally:
        reset_meter(token)
    assert len(calls) == 2
    assert {call["purpose"] for call in calls} == {"statement_ocr"}
    assert {call["run_id"] for call in calls} == {"run-1"}


def test_statement_ocr_records_the_returned_model():
    class Client(LlmClient):
        model = "gpt-4.1-mini"
        provider = "openrouter"

        def complete_json(self, request: LlmRequest) -> LlmResponse:
            return LlmResponse(
                {
                    "unreadable": False,
                    "opening_balance_vnd": None,
                    "closing_balance_vnd": None,
                    "lines": [],
                },
                "gpt-4.1-mini-2025-04-14",
                {"prompt_tokens": 1_000_000, "completion_tokens": 0, "total_tokens": 1_000_000},
            )

    parser = VisionStatementParser(
        Client(), SimpleNamespace(system="s", temperature=0, version="v")
    )
    token = open_scope("case-1", lambda: CLOCK)
    try:
        _, bucket = bind_run("case-1", "run-1", lambda: CLOCK)
        parser._ask("data:image/png;base64,eA==", 1, 1, False)
        (call,) = bucket.snapshot("run-1")
    finally:
        reset_meter(token)
    assert call["purpose"] == "statement_ocr"
    assert call["provider"] == "openrouter"
    assert call["model"] == "gpt-4.1-mini-2025-04-14"
    assert call["cost_micro_usd"] == 400_000


def test_traffic_aggregates_window_latency_and_cost():
    legacy = _case(
        "legacy",
        "2026-10-21T16:00:00+00:00",
        "REJECTED",
        [{"run_id": "old", "trigger": "INITIAL", "run_no": 1}],
        [{"model": "legacy-model"}, {"model": None}, {"model": "legacy-model"}],
        "HARD_POLICY_FAILURE",
    )
    free = _case(
        "free",
        "2026-10-21T17:30:00+00:00",
        "ACCEPTED",
        [
            _metered("free-1", "INITIAL", 1, 1000),
            _metered("free-2", "COUNTER", 2, 90_000),
        ],
    )
    priced = _case(
        "priced",
        "2026-10-22T01:00:00+00:00",
        "ACCEPTED",
        [_metered("priced-1", "INITIAL", 1, 3000, [_call("gpt-4.1-mini", 400_000)] * 2)],
    )
    unknown = _case(
        "unknown",
        "2026-10-22T02:00:00+00:00",
        "PENDING_REVIEW",
        [_metered("unknown-1", "INITIAL", 1, 5000, [_call("typesafe/jev-1.14", None)])],
    )
    report = build_traffic([legacy, free, priced, unknown], CLOCK, 2)
    assert PHONE not in json.dumps(report) and "MEMO_SECRET_TEXT" not in json.dumps(report)
    assert report["targets"] == {"latency_ms": 90_000, "cost_micro_usd": 100_000}
    assert report["summary"]["cases"] == 4
    assert report["summary"]["opened_today"] == 3
    assert report["summary"]["measured_runs"] == 3
    assert report["summary"]["unmeasured_runs"] == 1
    assert report["summary"]["latency_ms"] == {"p50": 3000, "p95": 5000}
    assert report["summary"]["cost"] == {
        "known_cases": 2,
        "unknown_cases": 2,
        "total_micro_usd": 800_000,
        "p50_micro_usd": 0,
    }
    assert report["by_day"] == [
        {"day": "2026-10-21", "cases": 1},
        {"day": "2026-10-22", "cases": 3},
    ]
    assert report["by_status"] == [
        {"status": "ACCEPTED", "cases": 2},
        {"status": "PENDING_REVIEW", "cases": 1},
        {"status": "REJECTED", "cases": 1},
    ]
    assert report["by_model"] == [
        {"model": "gpt-4.1-mini", "calls": 2, "cases": 1},
        {"model": "typesafe/jev-1.14", "calls": 1, "cases": 1},
    ]
    assert [row["case_id"] for row in report["recent"]] == ["unknown", "priced"]
    assert report["recent"][0]["cost_known"] is False
    assert report["recent"][0]["cost_micro_usd"] is None
    assert report["recent"][1]["cost_known"] is True
    assert report["recent"][1]["cost_micro_usd"] == 800_000
    assert report["recent"][1]["first_run"]["duration_ms"] == 3000
    repo = MemoryCaseRepository()
    for index, case in enumerate((legacy, free, priced, unknown)):
        repo.create(case, f"window-{index}", f"hash-{index}")
    window = _service(repo).traffic("2026-10-22", "2026-10-22", 12)
    assert {row["case_id"] for row in window["recent"]} == {"free", "priced", "unknown"}
    assert window["summary"]["opened_today"] == 3


def test_old_run_is_unmeasured_and_document_model_is_only_a_label():
    case = _case(
        "old",
        "2026-10-22T01:00:00+00:00",
        "REJECTED",
        [
            {"run_id": "a", "trigger": "INITIAL", "run_no": 1},
            _metered("b", "COUNTER", 2, 5, [_call("gpt-4.1-mini", 10)]),
        ],
        [{"model": "legacy-model"}],
    )
    report = build_traffic([case], CLOCK, 12)
    assert report["recent"][0]["first_run"]["duration_ms"] is None
    assert report["recent"][0]["models"] == ["gpt-4.1-mini"]
    assert report["recent"][0]["cost_known"] is False
    assert report["summary"]["measured_runs"] == 0
    assert report["summary"]["cost"]["total_micro_usd"] is None
    assert report["by_model"] == [{"model": "gpt-4.1-mini", "calls": 1, "cases": 1}]
    bare = _case(
        "bare",
        "2026-10-22T01:00:00+00:00",
        "NO_LOAN_NEEDED",
        [{"run_id": "s", "trigger": "STATEMENT", "run_no": 1, **_metered("s", "STATEMENT", 1, 10)}],
    )
    bare_report = build_traffic([bare], CLOCK, 12)
    assert bare_report["recent"][0]["first_run"] is None
    assert bare_report["recent"][0]["cost_known"] is True
    assert bare_report["recent"][0]["cost_micro_usd"] == 0
    assert bare_report["summary"]["measured_runs"] == 0
    empty = build_traffic([], CLOCK, 12)
    assert empty["summary"]["latency_ms"] == {"p50": None, "p95": None}
    assert empty["summary"]["cost"]["total_micro_usd"] is None
    assert empty["summary"]["cost"]["p50_micro_usd"] is None


def test_window_rejects_bad_dates_and_more_than_5000_cases():
    workflow = _service()
    with pytest.raises(WorkflowError) as error:
        workflow.traffic("2026-10-23", "2026-10-22", 12)
    assert (error.value.code, error.value.status) == ("TRAFFIC_WINDOW_INVALID", 422)
    with pytest.raises(WorkflowError) as error:
        workflow.traffic("2026-02-31", None, 12)
    assert error.value.code == "TRAFFIC_WINDOW_INVALID"
    repo = MemoryCaseRepository()
    stamp = "2026-10-22T01:00:00+00:00"
    for index in range(5001):
        repo.create(Case(str(uuid4()), "s", "s", {}, stamp), str(index), str(index))
    with pytest.raises(WorkflowError) as error:
        _service(repo).traffic(None, None, 12)
    assert (error.value.code, error.value.status) == ("TRAFFIC_WINDOW_TOO_WIDE", 503)


def test_cic_gate_without_usage_keeps_the_case_cost_unknown():
    def decide(state, questions, timeout):
        return {"eligible": {"noul": 0.9}}

    workflow = _service(cic_gate=JevCicGate(decide=decide))
    case = workflow.create(Plan(**plan()), "jev-traffic")
    (call,) = case.runs[0]["model_calls"]
    assert call["purpose"] == "cic_gate"
    assert call["provider"] == "openrouter"
    assert call["model"] == jev.MODEL
    assert call["cached"] is False
    assert call["prompt_tokens"] is None and call["cost_micro_usd"] is None
    report = workflow.traffic(None, None, 12)
    assert report["summary"]["cost"]["known_cases"] == 0
    assert report["summary"]["cost"]["total_micro_usd"] is None
    assert report["by_model"] == [{"model": jev.MODEL, "calls": 1, "cases": 1}]
    assert report["recent"][0]["cost_known"] is False


def test_new_case_traffic_measures_duration_and_zero_model_cost(client):  # noqa: F811
    created = create(client)
    detail = client.get(f"/v1/cases/{created['case_id']}", headers=BANK).json()
    run = detail["runs"][0]
    assert run["trigger"] == "INITIAL"
    assert run["duration_ms"] == 0
    assert run["model_calls"] == []
    body = client.get("/v1/studio/traffic", headers=BANK).json()
    assert body["zone"] == "Asia/Ho_Chi_Minh"
    assert body["summary"]["cases"] == 1
    assert body["summary"]["opened_today"] == 1
    assert body["summary"]["measured_runs"] == 1
    assert body["summary"]["latency_ms"] == {"p50": 0, "p95": 0}
    assert body["summary"]["cost"]["known_cases"] == 1
    assert body["summary"]["cost"]["total_micro_usd"] == 0
    item = body["recent"][0]
    assert item["case_id"] == created["case_id"]
    assert item["cost_known"] is True and item["cost_micro_usd"] == 0
    assert item["models"] == []
    assert item["first_run"]["duration_ms"] == 0
    assert "plan" not in body and "underwriter_memo" not in json.dumps(body)
    empty = client.get(
        "/v1/studio/traffic", headers=BANK, params={"from": "2026-10-23", "to": "2026-10-23"}
    ).json()
    assert empty["summary"]["cases"] == 0
    assert empty["summary"]["cost"]["total_micro_usd"] is None
    later = client.get(
        "/v1/studio/traffic",
        headers=BANK,
        params={"from": "2026-10-23", "to": "2026-10-22"},
    )
    assert later.status_code == 422
    assert later.json()["detail"]["code"] == "TRAFFIC_WINDOW_INVALID"
    bad = client.get("/v1/studio/traffic", headers=BANK, params={"from": "22/10/2026"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "TRAFFIC_WINDOW_INVALID"
    seller = client.get("/v1/studio/traffic", headers=seller_headers())
    assert seller.status_code == 403
    assert seller.json()["detail"]["code"] == "UNDERWRITER_REQUIRED"
