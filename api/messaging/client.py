import json
import sys
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from kafka import KafkaConsumer, KafkaProducer, OffsetAndMetadata
from kafka.errors import KafkaError, KafkaTimeoutError

import config

from .config import KafkaClientError, KafkaSettings, kafka_settings


@dataclass(frozen=True)
class Message:
    topic: str
    key: str | None
    value: dict
    partition: int = 0
    offset: int = 0


Handler = Callable[[Message], None]


def encode(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def decode(raw: bytes | None) -> dict:
    try:
        value = json.loads(raw or b"")
    except ValueError as exc:
        raise KafkaClientError("KAFKA_INVALID_MESSAGE") from exc
    if not isinstance(value, dict):
        raise KafkaClientError("KAFKA_INVALID_MESSAGE")
    return value


class MessageClient(ABC):
    @abstractmethod
    def publish(self, topic: str, value: dict, key: str | None = None) -> None: ...

    @abstractmethod
    def flush(self, timeout: float = 10.0) -> None: ...

    @abstractmethod
    def consume(
        self, topics: Iterable[str], handler: Handler, max_messages: int = 100, timeout: float = 1.0
    ) -> int: ...

    @abstractmethod
    def close(self) -> None: ...


class KafkaClient(MessageClient):
    def __init__(self, settings: KafkaSettings, producer=None, consumer=None):
        self.settings = settings
        self._producer, self._consumer = producer, consumer
        self._subscribed: tuple[str, ...] = ()
        self._failures: list[str] = []

    def _get_producer(self):
        if self._producer is None:
            self._producer = KafkaProducer(**self.settings.producer())
        return self._producer

    def _get_consumer(self, topics: tuple[str, ...]):
        if self._consumer is None:
            self._consumer = KafkaConsumer(**self.settings.consumer())
        if topics != self._subscribed:
            self._consumer.subscribe(list(topics))
            self._subscribed = topics
        return self._consumer

    def _on_error(self, error: Exception) -> None:
        self._failures.append(str(error))

    def publish(self, topic: str, value: dict, key: str | None = None) -> None:
        producer = self._get_producer()
        try:
            future = producer.send(
                topic, value=encode(value), key=key.encode() if key is not None else None
            )
        except KafkaTimeoutError as exc:
            self._discard_producer()
            raise KafkaClientError("KAFKA_QUEUE_FULL") from exc
        future.add_errback(self._on_error)

    def flush(self, timeout: float = 10.0) -> None:
        if self._producer is None:
            return
        try:
            self._producer.flush(timeout)
            timed_out = False
        except KafkaTimeoutError:
            timed_out = True
        failures, self._failures = self._failures, []
        if timed_out or failures:
            self._discard_producer()
            raise KafkaClientError("KAFKA_DELIVERY_FAILED")

    def _discard_producer(self) -> None:
        # Unsent records would go out again next to the caller's retry, one copy per failed attempt.
        producer, self._producer = self._producer, None
        if producer is not None:
            producer.close(timeout=0)

    def consume(
        self, topics: Iterable[str], handler: Handler, max_messages: int = 100, timeout: float = 1.0
    ) -> int:
        consumer = self._get_consumer(tuple(topics))
        handled = 0
        while handled < max_messages:
            try:
                batch = consumer.poll(timeout_ms=int(timeout * 1000), max_records=1)
            except KafkaError as exc:
                raise KafkaClientError("KAFKA_CONSUMER_FAILED") from exc
            if not batch:
                break
            for partition, records in batch.items():
                for record in records:
                    key = record.key
                    try:
                        handler(
                            Message(
                                record.topic,
                                key.decode() if key is not None else None,
                                decode(record.value),
                                record.partition,
                                record.offset,
                            )
                        )
                    except Exception:
                        consumer.seek(partition, record.offset)
                        raise
                    consumer.commit({partition: OffsetAndMetadata(record.offset + 1, None, -1)})
                    handled += 1
        return handled

    def close(self) -> None:
        try:
            self.flush()
        finally:
            if self._consumer is not None:
                self._consumer.close()
                self._consumer, self._subscribed = None, ()
            if self._producer is not None:
                self._producer.close()
                self._producer = None


class MemoryClient(MessageClient):
    def __init__(self):
        self.topics: dict[str, list[Message]] = defaultdict(list)
        self._offsets: dict[str, int] = defaultdict(int)

    def publish(self, topic: str, value: dict, key: str | None = None) -> None:
        log = self.topics[topic]
        log.append(Message(topic, key, decode(encode(value)), 0, len(log)))

    def flush(self, timeout: float = 10.0) -> None:
        return None

    def consume(
        self, topics: Iterable[str], handler: Handler, max_messages: int = 100, timeout: float = 1.0
    ) -> int:
        handled = 0
        for topic in topics:
            log = self.topics[topic]
            while handled < max_messages and self._offsets[topic] < len(log):
                handler(log[self._offsets[topic]])
                self._offsets[topic] += 1
                handled += 1
        return handled

    def close(self) -> None:
        return None


CLIENTS: dict[str, Callable[[], MessageClient]] = {
    "memory": MemoryClient,
    "kafka": lambda: KafkaClient(kafka_settings()),
}


def build_message_client(kind: str | None = None) -> MessageClient:
    kind = kind or config.MESSAGE_BUS
    if kind not in CLIENTS:
        raise KafkaClientError("MESSAGE_BUS_UNKNOWN")
    return CLIENTS[kind]()


if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else "riverbank.smoke"
    client = build_message_client("kafka")
    client.publish(topic, {"event": "smoke", "source": "messaging.client"}, key="smoke")
    client.flush()
    client.consume([topic], print, max_messages=1, timeout=10.0)
    client.close()
