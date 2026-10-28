import logging
import time
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
    retention_hours: float = 336.0
    prune_seconds: float = 3600.0


def relay_config() -> RelayConfig:
    return RelayConfig(
        config.CASE_EVENTS_TOPIC,
        config.EVENT_RELAY_BATCH_SIZE,
        config.EVENT_RELAY_IDLE_SECONDS,
        config.EVENT_RELAY_RETRY_SECONDS,
        config.EVENT_OUTBOX_RETENTION_HOURS,
        config.EVENT_OUTBOX_PRUNE_SECONDS,
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

    def prune(self) -> int:
        removed = self.outbox.prune_published(self.cfg.retention_hours)
        if removed:
            log.info("event outbox pruned %d published rows", removed)
        return removed

    def run(self, stop: Event) -> None:
        next_prune = time.monotonic()
        try:
            while not stop.is_set():
                try:
                    if time.monotonic() >= next_prune:
                        self.prune()
                        next_prune = time.monotonic() + self.cfg.prune_seconds
                    sent = self.run_once()
                except Exception:
                    log.exception("event relay failed, events stay in the outbox")
                    stop.wait(self.cfg.retry_seconds)
                    continue
                if sent < self.cfg.batch_size:
                    stop.wait(self.cfg.idle_seconds)
        finally:
            self.bus.close()
