import logging
from datetime import timedelta

import pytest

import config
import llm
from services.ratelimit import RateLimit, RateLimiter
from services.underwriting.models import WorkflowError
from services.underwriting.service import utc_now
from tests.test_underwriting import client, plan, seller_headers, service  # noqa: F401


def test_rate_limit_parses_and_disables_on_zero():
    assert RateLimit.parse("20/60") == RateLimit(20, 60)
    assert not RateLimit.parse("0/60").enabled
    limiter, now = RateLimiter(RateLimit(0, 60)), utc_now()
    for _ in range(5):
        limiter.hit("a", now)


def test_rate_limiter_slides_and_reports_retry_after():
    limiter, now = RateLimiter(RateLimit(2, 60)), utc_now()
    limiter.hit("a", now)
    limiter.hit("a", now + timedelta(seconds=10))
    limiter.hit("b", now)
    with pytest.raises(WorkflowError) as error:
        limiter.hit("a", now + timedelta(seconds=20))
    assert (error.value.code, error.value.status) == ("RATE_LIMITED", 429)
    assert error.value.headers == {"Retry-After": "40"}
    limiter.hit("a", now + timedelta(seconds=61))


def test_create_case_returns_429_with_retry_after(client, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RIVERBANK_RATE_LIMIT_CASE_CREATE", "1/60")
    first = client.post("/v1/cases", json=plan(), headers=seller_headers(key="a"))
    assert first.status_code == 201, first.text
    second = client.post("/v1/cases", json=plan(), headers=seller_headers(key="b"))
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "RATE_LIMITED"
    assert 1 <= int(second.headers["Retry-After"]) <= 60
    other = client.post("/v1/cases", json=plan("demo-a"), headers=seller_headers("demo-a", key="c"))
    assert other.status_code == 201, other.text


class Captured(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_requests_carry_request_id_and_are_timed(client):  # noqa: F811
    captured, log = Captured(), logging.getLogger("riverbank.access")
    log.addHandler(captured)
    try:
        fresh = client.get("/")
        echoed = client.get("/", headers={"X-Request-ID": "fe-123"})
        rejected = client.get("/", headers={"X-Request-ID": "bad id\nforged"})
        client.get("/health")
    finally:
        log.removeHandler(captured)
    assert len(fresh.headers["X-Request-ID"]) == 32
    assert echoed.headers["X-Request-ID"] == "fe-123"
    assert rejected.headers["X-Request-ID"] != "bad id\nforged"
    assert len(captured.lines) == 3
    assert '"request_id": "fe-123"' in captured.lines[1]
    assert '"path": "/"' in captured.lines[0] and '"duration_ms"' in captured.lines[0]


def test_llm_limits_per_purpose(monkeypatch):
    assert config.llm_limits("memo_writer") == (15.0, 1)
    assert config.llm_limits("statement_ocr") == (
        config.LLM_TIMEOUT_SECONDS,
        config.LLM_MAX_RETRIES,
    )
    monkeypatch.setenv("LLM_MEMO_WRITER_TIMEOUT_SECONDS", "9")
    assert config.llm_limits("memo_writer") == (9.0, 1)
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "LLM_CACHE_DIR", "")
    built = llm.build_llm_client("memo_writer")
    assert built._client.timeout == 9.0 and built._client.max_retries == 1
