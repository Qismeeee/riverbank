import json
import logging
import re
import time
from uuid import uuid4

import config
from logs import stdout_logger

REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
QUIET_PATHS = frozenset({"/health"})


def access_logger() -> logging.Logger:
    return stdout_logger("riverbank.access")


class RequestTiming:
    def __init__(self, app, log: logging.Logger | None = None):
        self.app, self.log = app, log or access_logger()

    @staticmethod
    def _request_id(scope) -> str:
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        return incoming if REQUEST_ID.match(incoming) else uuid4().hex

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id, start, status = self._request_id(scope), time.perf_counter(), 500

        async def tagged(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-request-id", request_id.encode()),
                ]
            await send(message)

        try:
            await self.app(scope, receive, tagged)
        finally:
            if scope["path"] not in QUIET_PATHS:
                duration = round((time.perf_counter() - start) * 1000, 1)
                self.log.info(
                    json.dumps(
                        {
                            "event": "request",
                            "request_id": request_id,
                            "method": scope["method"],
                            "path": scope["path"],
                            "status": status,
                            "duration_ms": duration,
                            "slow": duration >= config.SLOW_REQUEST_MS,
                        }
                    )
                )
