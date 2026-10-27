from threading import Event, Thread

import pytest

import config
from messaging import KafkaClientError, MemoryClient
from routers.events import start_relay
from services.events import EventRelay, RelayConfig, relay_config
from services.underwriting.repository import EVENT_SCHEMA, MemoryCaseRepository
from services.underwriting.risk import BaselineRiskModel
from tests.test_underwriting_risk import build, create

TOPIC = "riverbank.case-events"


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    monkeypatch.setattr(config, "ENV", "local")
    monkeypatch.setattr(config, "WORKFLOW_MODE", "enabled")
    monkeypatch.setattr(config, "EVENT_RELAY", "off")


class FlakyBus(MemoryClient):
    def __init__(self, failures: int):
        super().__init__()
        self.failures = failures

    def flush(self, timeout: float = 10.0) -> None:
        if self.failures:
            self.failures -= 1
            raise KafkaClientError("KAFKA_DELIVERY_FAILED")


def drain(repository) -> list[dict]:
    with repository.publishing(1000) as batch:
        return batch


def relay(repository, bus=None, batch_size=100):
    return EventRelay(repository, bus or MemoryClient(), RelayConfig(TOPIC, batch_size, 0, 0))


def test_new_case_queues_its_events_once():
    service = build(BaselineRiskModel())
    case = create(service)
    with service.repository.publishing(100) as batch:
        assert [m["event_id"] for m in batch] == [e["event_id"] for e in case.events]
        assert [m["ordinal"] for m in batch] == list(range(len(case.events)))
    assert drain(service.repository) == []


def test_transaction_queues_only_new_events():
    service = build(BaselineRiskModel())
    case = create(service)
    drain(service.repository)
    with service.repository.transaction(case.case_id, case.version) as locked:
        locked.events.append({**locked.events[-1], "event_id": "e-new", "reason": "TEST"})
    assert [m["event_id"] for m in drain(service.repository)] == ["e-new"]


def test_failed_transaction_queues_nothing():
    service = build(BaselineRiskModel())
    case = create(service)
    drain(service.repository)
    with pytest.raises(RuntimeError):
        with service.repository.transaction(case.case_id, case.version) as locked:
            locked.events.append({**locked.events[-1], "event_id": "e-lost"})
            raise RuntimeError()
    assert drain(service.repository) == []


def test_failed_batch_stays_pending():
    service = build(BaselineRiskModel())
    case = create(service)
    with pytest.raises(RuntimeError):
        with service.repository.publishing(100):
            raise RuntimeError()
    assert len(drain(service.repository)) == len(case.events)


def test_publishing_does_not_block_cases_and_keeps_new_events():
    service = build(BaselineRiskModel())
    first = create(service)
    created = []
    with service.repository.publishing(100) as batch:
        worker = Thread(target=lambda: created.append(create(service, "ducthinh.tech")))
        worker.start()
        worker.join(timeout=5)
        assert created, "case write blocked while a batch was being published"
    assert len(batch) == len(first.events)
    assert [m["event_id"] for m in drain(service.repository)] == [
        e["event_id"] for e in created[0].events
    ]


def test_message_carries_only_the_event():
    service = build(BaselineRiskModel())
    case = create(service)
    message = drain(service.repository)[0]
    assert set(message) == {"schema", "case_id", "ordinal", *case.events[0]}
    assert (message["schema"], message["case_id"]) == (EVENT_SCHEMA, case.case_id)
    for private in ("plan", "features", "consent", "seller_id", "requested_amount"):
        assert private not in message


def test_relay_publishes_in_order_keyed_by_case():
    service = build(BaselineRiskModel())
    first, second = create(service), create(service, "ducthinh.tech")
    bus = MemoryClient()
    sent = relay(service.repository, bus).run_once()
    log = bus.topics[TOPIC]
    assert sent == len(first.events) + len(second.events) == len(log)
    assert [m.key for m in log] == [m.value["case_id"] for m in log]
    for case in (first, second):
        ordinals = [m.value["ordinal"] for m in log if m.key == case.case_id]
        assert ordinals == sorted(ordinals)
    assert {m.value["event_type"] for m in log} == {"STATE_CHANGED"}
    assert relay(service.repository, bus).run_once() == 0


def test_relay_respects_batch_size():
    service = build(BaselineRiskModel())
    case = create(service)
    bus = MemoryClient()
    assert relay(service.repository, bus, batch_size=1).run_once() == 1
    assert len(bus.topics[TOPIC]) == 1
    assert relay(service.repository, bus).run_once() == len(case.events) - 1


def test_bus_failure_keeps_events_for_retry():
    service = build(BaselineRiskModel())
    case = create(service)
    bus = FlakyBus(failures=1)
    with pytest.raises(KafkaClientError):
        relay(service.repository, bus).run_once()
    assert relay(service.repository, bus).run_once() == len(case.events)


def test_relay_loop_retries_after_failure_and_closes_bus():
    service = build(BaselineRiskModel())
    create(service)
    stop = Event()

    class StopAfterSend(FlakyBus):
        def flush(self, timeout: float = 10.0) -> None:
            super().flush(timeout)
            stop.set()

        def close(self) -> None:
            self.closed = True

    bus = StopAfterSend(failures=1)
    relay(service.repository, bus).run(stop)
    assert bus.closed and bus.topics[TOPIC]
    assert drain(service.repository) == []


def test_empty_outbox_does_not_flush():
    class NoFlush(MemoryClient):
        def flush(self, timeout: float = 10.0) -> None:
            raise AssertionError("flush without messages")

    assert relay(MemoryCaseRepository(), NoFlush()).run_once() == 0


def test_relay_config_reads_app_config(monkeypatch):
    monkeypatch.setattr(config, "CASE_EVENTS_TOPIC", "events.test")
    monkeypatch.setattr(config, "EVENT_RELAY_BATCH_SIZE", 7)
    cfg = relay_config()
    assert (cfg.topic, cfg.batch_size) == ("events.test", 7)


def test_relay_off_by_default(monkeypatch):
    monkeypatch.setattr(config, "EVENT_RELAY", "off")
    assert start_relay() is None
