import logging
from dataclasses import dataclass
from threading import Event

import config
from messaging import MessageClient
from services.underwriting.repository import CaseRepository

log = logging.getLogger("riverbank.events")


@dataclass(frozen=True)
class RelayConfig:
    topic: str = "riverbank.case-events"
    batch_size: int = 100
    idle_seconds: float = 1.0
    retry_seconds: float = 10.0


def relay_config() -> RelayConfig:
    return RelayConfig(
        config.CASE_EVENTS_TOPIC,
        config.EVENT_RELAY_BATCH_SIZE,
        config.EVENT_RELAY_IDLE_SECONDS,
        config.EVENT_RELAY_RETRY_SECONDS,
    )


class EventRelay:
    def __init__(self, outbox: CaseRepository, bus: MessageClient, cfg: RelayConfig | None = None):
        self.outbox, self.bus, self.cfg = outbox, bus, cfg or RelayConfig()

    def run_once(self) -> int:
        with self.outbox.publishing(self.cfg.batch_size) as messages:
            for message in messages:
                self.bus.publish(self.cfg.topic, message, key=message["case_id"])
            if messages:
                self.bus.flush()
        return len(messages)

    def run(self, stop: Event) -> None:
        try:
            while not stop.is_set():
                try:
                    sent = self.run_once()
                except Exception:
                    log.exception("event relay failed, events stay in the outbox")
                    stop.wait(self.cfg.retry_seconds)
                    continue
                if sent < self.cfg.batch_size:
                    stop.wait(self.cfg.idle_seconds)
        finally:
            self.bus.close()
