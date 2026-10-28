import logging
from threading import Event, Thread

import config
from messaging import build_message_client
from routers.underwriting import get_service
from services.events import EventRelay, relay_config

log = logging.getLogger("riverbank.events")


def start_relay() -> Event | None:
    if config.EVENT_RELAY != "on":
        return None
    if config.MESSAGE_BUS == "memory":
        log.error("event relay needs RIVERBANK_MESSAGE_BUS=kafka, memory would drop every event")
        return None
    stop = Event()

    def run() -> None:
        while not stop.is_set():
            try:
                relay = EventRelay(get_service().repository, build_message_client(), relay_config())
            except Exception:
                log.exception("event relay cannot start")
                stop.wait(30)
                continue
            relay.run(stop)

    Thread(target=run, name="riverbank-events", daemon=True).start()
    return stop
