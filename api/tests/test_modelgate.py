import httpx
import pytest

import config
import jev
from kv import MemoryKVClient, ValkeyClientError
from llm import LlmError, LlmRequest, OpenAICompatibleClient
from services.modelgate import GateLimits, GateRejected, ModelGate, configured_limits, model_gate
from services.underwriting.memo import Draft, FallbackMemoWriter, MemoRequest, MemoWriter


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Transient(Exception):
    pass


def transient(exc):
    return isinstance(exc, Transient)


def gate(clock=None, **limits):
    clock = clock or Clock()
    defaults = {"wait_seconds": 0, "per_minute": 0} | limits
    return ModelGate(
        MemoryKVClient(clock), lambda p: GateLimits(**defaults), clock=clock, sleep=clock.sleep
    )


def boom():
    raise Transient()


def test_call_returns_result():
    assert gate().call("p", lambda: 42, transient) == 42


def test_disabled_gate_calls_through():
    g = gate(concurrency=1)
    g.enabled = False
    g.kv.set("gate:open:p", "1")
    assert g.call("p", lambda: "ok", transient) == "ok"


def test_busy_when_all_slots_taken():
    g = gate(concurrency=1)
    g._acquire("p", g.limits("p"), "other")
    with pytest.raises(GateRejected, match="MODEL_BUSY"):
        g.call("p", lambda: "ok", transient)


def test_slot_released_after_success_and_error():
    g = gate(concurrency=1)
    g.call("p", lambda: "ok", transient)
    with pytest.raises(Transient):
        g.call("p", boom, transient)
    assert g.call("p", lambda: "ok", transient) == "ok"


def test_waits_for_slot_to_free():
    clock = Clock()
    g = gate(clock, concurrency=1, wait_seconds=1)
    g.kv.set("gate:slot:p:0", "other", ttl=1)
    assert g.call("p", lambda: "ok", transient) == "ok"


def test_expired_lease_frees_slot():
    clock = Clock()
    g = gate(clock, concurrency=1, lease_seconds=5)
    g._acquire("p", g.limits("p"), "crashed")
    clock.now += 5
    assert g.call("p", lambda: "ok", transient) == "ok"


def test_release_does_not_free_someone_elses_slot():
    g = gate(concurrency=1)
    g.kv.set("gate:slot:p:0", "other")
    assert not g.kv.delete_if("gate:slot:p:0", "mine")
    assert g.kv.get("gate:slot:p:0") == "other"


def test_per_minute_budget():
    clock = Clock()
    clock.now = 60 * 1000
    g = gate(clock, per_minute=2)
    g.call("p", lambda: 1, transient)
    g.call("p", lambda: 2, transient)
    with pytest.raises(GateRejected, match="MODEL_RATE_LIMITED"):
        g.call("p", lambda: 3, transient)
    clock.now += 60
    assert g.call("p", lambda: 4, transient) == 4


def test_providers_are_independent():
    g = gate(breaker_failures=1)
    with pytest.raises(Transient):
        g.call("openai", boom, transient)
    assert g.call("jev", lambda: "ok", transient) == "ok"


def test_breaker_opens_after_failures_and_recovers():
    clock = Clock()
    g = gate(clock, breaker_failures=2, breaker_window_seconds=60, breaker_open_seconds=30)
    for _ in range(2):
        with pytest.raises(Transient):
            g.call("p", boom, transient)
    calls = []
    with pytest.raises(GateRejected, match="MODEL_CIRCUIT_OPEN"):
        g.call("p", lambda: calls.append(1), transient)
    assert calls == []
    clock.now += 30
    assert g.call("p", lambda: "ok", transient) == "ok"
    with pytest.raises(Transient):
        g.call("p", boom, transient)
    assert g.call("p", lambda: "still closed", transient) == "still closed"


def test_failed_probe_reopens_breaker():
    clock = Clock()
    g = gate(clock, breaker_failures=2, breaker_window_seconds=60, breaker_open_seconds=30)
    for _ in range(2):
        with pytest.raises(Transient):
            g.call("p", boom, transient)
    clock.now += 30
    with pytest.raises(Transient):
        g.call("p", boom, transient)
    with pytest.raises(GateRejected, match="MODEL_CIRCUIT_OPEN"):
        g.call("p", lambda: "ok", transient)


def test_non_transient_errors_do_not_open_breaker():
    g = gate(breaker_failures=1)

    def bad():
        raise ValueError()

    for _ in range(3):
        with pytest.raises(ValueError):
            g.call("p", bad, transient)
    assert g.call("p", lambda: "ok", transient) == "ok"


def test_success_resets_failure_count():
    g = gate(breaker_failures=2)
    with pytest.raises(Transient):
        g.call("p", boom, transient)
    g.call("p", lambda: "ok", transient)
    with pytest.raises(Transient):
        g.call("p", boom, transient)
    assert g.call("p", lambda: "ok", transient) == "ok"


def down(*args, **kwargs):
    raise ValkeyClientError("VALKEY_UNAVAILABLE")


class DownStore(MemoryKVClient):
    set = delete = delete_if = exists = incr = expire = down


def test_store_down_fails_open():
    g = ModelGate(DownStore(), lambda p: GateLimits(concurrency=1, per_minute=1))
    assert g.call("p", lambda: "ok", transient) == "ok"
    with pytest.raises(Transient):
        g.call("p", boom, transient)
    assert g.call("p", lambda: "again", transient) == "again"


def test_configured_limits_read_env(monkeypatch):
    monkeypatch.setenv("RIVERBANK_MODEL_GATE_JEV_CONCURRENCY", "3")
    monkeypatch.setenv("RIVERBANK_MODEL_GATE_BREAKER_OPEN_SECONDS", "45")
    limits = configured_limits("jev")
    assert limits.concurrency == 3 and limits.breaker_open_seconds == 45
    assert configured_limits("openai").concurrency == config.MODEL_GATE_DEFAULTS["openai"][0]


def test_model_gate_follows_config(monkeypatch):
    monkeypatch.setattr(config, "MODEL_GATE", "off")
    monkeypatch.setattr(config, "MODEL_GATE_STORE", "memory")
    assert not model_gate().enabled


def test_jev_transient_errors():
    request = httpx.Request("POST", jev.URL)

    def status(code):
        return httpx.HTTPStatusError("x", request=request, response=httpx.Response(code))

    assert jev.transient(httpx.ConnectTimeout("x"))
    assert jev.transient(status(503)) and jev.transient(status(429))
    assert not jev.transient(status(401)) and not jev.transient(ValueError())


def test_jev_decide_goes_through_gate(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-or-test")
    g = gate()
    g.kv.set("gate:open:jev", "1")
    monkeypatch.setattr(jev, "model_gate", lambda: g)
    with pytest.raises(GateRejected, match="MODEL_CIRCUIT_OPEN"):
        jev.decide(["state"], {})


def test_llm_client_maps_rejection_to_llm_error():
    g = gate()
    g.kv.set("gate:open:openai", "1")
    client = OpenAICompatibleClient("sk-test", "m", gate=g, provider="openai")
    sent = []
    client._client.chat.completions.create = lambda **kwargs: sent.append(kwargs)
    with pytest.raises(LlmError, match="MODEL_CIRCUIT_OPEN"):
        client.complete_json(LlmRequest("s", {}, {}, "n"))
    assert sent == []


def test_llm_outage_opens_breaker():
    from openai import APIConnectionError

    g = gate(breaker_failures=2)
    client = OpenAICompatibleClient("sk-test", "m", gate=g, provider="openai")
    calls = []

    def down(**kwargs):
        calls.append(1)
        raise APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))

    client._client.chat.completions.create = down
    codes = []
    for _ in range(3):
        with pytest.raises(LlmError) as err:
            client.complete_json(LlmRequest("s", {}, {}, "n"))
        codes.append(err.value.code)
    assert codes == ["LLM_UNAVAILABLE", "LLM_UNAVAILABLE", "MODEL_CIRCUIT_OPEN"]
    assert len(calls) == 2


def test_memo_falls_back_when_breaker_open():
    class Primary(MemoWriter):
        def write(self, request):
            raise LlmError("MODEL_CIRCUIT_OPEN")

    class Template(MemoWriter):
        def write(self, request):
            return Draft({}, "template", "v1")

    draft = FallbackMemoWriter(Primary(), Template()).write(MemoRequest("REVIEW", [], [], 1, 2))
    assert draft.generator == "template" and draft.notes == ["MODEL_CIRCUIT_OPEN"]
