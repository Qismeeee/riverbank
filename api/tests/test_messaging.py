from types import SimpleNamespace

import pytest
from kafka import TopicPartition
from kafka.errors import KafkaConnectionError, KafkaTimeoutError

from messaging import (
    KafkaClient,
    KafkaClientError,
    KafkaSettings,
    MemoryClient,
    build_message_client,
    kafka_settings,
)

PARTITION = TopicPartition("cases", 0)


class FakeFuture:
    def __init__(self, error):
        self.error = error

    def add_errback(self, callback):
        if self.error is not None:
            callback(self.error)


class FakeProducer:
    def __init__(self, fail_with=None, flush_timeout=False):
        self.sent, self.fail_with, self.flush_timeout = [], fail_with, flush_timeout
        self.closed = False

    def send(self, topic, value, key):
        self.sent.append((topic, key, value))
        return FakeFuture(self.fail_with)

    def flush(self, timeout):
        if self.flush_timeout:
            raise KafkaTimeoutError()

    def close(self, timeout=None):
        self.closed, self.close_timeout = True, timeout


def record(value, offset=0, key=b"shop-1"):
    return SimpleNamespace(topic="cases", partition=0, offset=offset, key=key, value=value)


class FakeConsumer:
    def __init__(self, *batches):
        self.batches, self.commits, self.seeks = list(batches), [], []
        self.subscriptions, self.closed = [], False

    def subscribe(self, topics):
        self.subscriptions.append(topics)

    def poll(self, timeout_ms, max_records):
        batch = self.batches.pop(0) if self.batches else {}
        if isinstance(batch, Exception):
            raise batch
        return batch

    def commit(self, offsets):
        self.commits += [(tp.partition, meta.offset) for tp, meta in offsets.items()]

    def seek(self, partition, offset):
        self.seeks.append((partition.partition, offset))

    def close(self):
        self.closed = True


SETTINGS = KafkaSettings("localhost:9092")


def test_settings_add_sasl_only_when_mechanism_is_set():
    assert "sasl_mechanism" not in SETTINGS.producer()
    assert SETTINGS.producer()["enable_idempotence"] is True
    secured = KafkaSettings("b1:9096, b2:9096", sasl_mechanism="SCRAM-SHA-512", username="u")
    assert secured.consumer()["bootstrap_servers"] == ["b1:9096", "b2:9096"]
    assert secured.consumer()["sasl_plain_username"] == "u"
    assert secured.consumer()["enable_auto_commit"] is False
    with pytest.raises(KafkaClientError, match="KAFKA_NOT_CONFIGURED"):
        KafkaSettings("").producer()


def test_publish_encodes_json_and_key():
    producer = FakeProducer()
    client = KafkaClient(SETTINGS, producer=producer)
    client.publish("cases", {"case_id": "c1", "note": "Đã duyệt"}, key="shop-1")
    client.flush()
    assert producer.sent == [("cases", b"shop-1", '{"case_id":"c1","note":"Đã duyệt"}'.encode())]


def test_flush_raises_when_delivery_fails_or_times_out():
    client = KafkaClient(SETTINGS, producer=FakeProducer(fail_with=KafkaConnectionError()))
    client.publish("cases", {"case_id": "c1"})
    with pytest.raises(KafkaClientError, match="KAFKA_DELIVERY_FAILED"):
        client.flush()
    slow = KafkaClient(SETTINGS, producer=FakeProducer(flush_timeout=True))
    slow.publish("cases", {"case_id": "c1"})
    with pytest.raises(KafkaClientError, match="KAFKA_DELIVERY_FAILED"):
        slow.flush()


@pytest.mark.parametrize(
    "producer", [FakeProducer(fail_with=KafkaConnectionError()), FakeProducer(flush_timeout=True)]
)
def test_failed_flush_drops_unsent_records(producer):
    client = KafkaClient(SETTINGS, producer=producer)
    client.publish("cases", {"case_id": "c1"})
    with pytest.raises(KafkaClientError):
        client.flush()
    assert producer.closed and producer.close_timeout == 0
    assert client._producer is None
    client.flush()


def test_consume_commits_each_message_after_handler():
    consumer = FakeConsumer(
        {PARTITION: [record(b'{"n":1}', offset=7)]}, {PARTITION: [record(b'{"n":2}', offset=8)]}
    )
    client = KafkaClient(SETTINGS, consumer=consumer)
    seen = []
    assert client.consume(["cases"], seen.append) == 2
    assert [(m.value, m.offset) for m in seen] == [({"n": 1}, 7), ({"n": 2}, 8)]
    assert {m.key for m in seen} == {"shop-1"}
    assert consumer.commits == [(0, 8), (0, 9)] and consumer.subscriptions == [["cases"]]


def test_consume_rewinds_and_does_not_commit_when_handler_fails():
    consumer = FakeConsumer({PARTITION: [record(b'{"n":1}', offset=4)]})

    def boom(message):
        raise RuntimeError("handler")

    with pytest.raises(RuntimeError):
        KafkaClient(SETTINGS, consumer=consumer).consume(["cases"], boom)
    assert consumer.commits == [] and consumer.seeks == [(0, 4)]


def test_consume_wraps_broker_errors():
    consumer = FakeConsumer(KafkaConnectionError())
    with pytest.raises(KafkaClientError, match="KAFKA_CONSUMER_FAILED"):
        KafkaClient(SETTINGS, consumer=consumer).consume(["cases"], lambda m: None)


def test_consume_rejects_non_object_payload():
    consumer = FakeConsumer({PARTITION: [record(b"[1,2]", offset=3)]})
    with pytest.raises(KafkaClientError, match="KAFKA_INVALID_MESSAGE"):
        KafkaClient(SETTINGS, consumer=consumer).consume(["cases"], lambda m: None)
    assert consumer.commits == [] and consumer.seeks == [(0, 3)]


def test_close_flushes_and_closes_both_sides():
    producer, consumer = FakeProducer(), FakeConsumer()
    KafkaClient(SETTINGS, producer=producer, consumer=consumer).close()
    assert producer.closed and consumer.closed


def test_memory_client_does_not_replay_consumed_messages():
    client = MemoryClient()
    client.publish("cases", {"n": 1}, key="a")
    client.publish("cases", {"n": 2}, key="b")
    seen = []
    assert client.consume(["cases"], seen.append, max_messages=1) == 1
    assert client.consume(["cases"], seen.append) == 1
    assert client.consume(["cases"], seen.append) == 0
    assert [(m.key, m.value, m.offset) for m in seen] == [("a", {"n": 1}, 0), ("b", {"n": 2}, 1)]


def test_kafka_settings_read_app_config(monkeypatch):
    import config

    monkeypatch.setattr(config, "KAFKA_BOOTSTRAP_SERVERS", "broker:9096")
    monkeypatch.setattr(config, "KAFKA_SASL_MECHANISM", "SCRAM-SHA-512")
    monkeypatch.setattr(config, "KAFKA_USERNAME", "riverbank")
    settings = kafka_settings()
    assert settings.producer()["bootstrap_servers"] == ["broker:9096"]
    assert settings.producer()["sasl_plain_username"] == "riverbank"


def test_build_message_client():
    assert isinstance(build_message_client("memory"), MemoryClient)
    assert isinstance(build_message_client("kafka"), KafkaClient)
    with pytest.raises(KafkaClientError, match="MESSAGE_BUS_UNKNOWN"):
        build_message_client("rabbit")
