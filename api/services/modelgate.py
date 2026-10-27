import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import TypeVar
from uuid import uuid4

import config
from kv import KVClient, ValkeyClientError, build_kv_client

log = logging.getLogger("riverbank.modelgate")
T = TypeVar("T")
POLL_SECONDS = 0.1


class GateRejected(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GateLimits:
    concurrency: int = 8
    per_minute: int = 120
    breaker_failures: int = 5
    breaker_window_seconds: int = 60
    breaker_open_seconds: int = 30
    wait_seconds: float = 2.0
    lease_seconds: int = 120


class ModelGate:
    def __init__(
        self,
        kv: KVClient,
        limits: Callable[[str], GateLimits] = lambda provider: GateLimits(),
        enabled: bool = True,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.kv, self.limits, self.enabled = kv, limits, enabled
        self.clock, self.sleep = clock, sleep

    def call(
        self, provider: str, fn: Callable[[], T], is_failure: Callable[[Exception], bool]
    ) -> T:
        if not self.enabled:
            return fn()
        limits = self.limits(provider)
        if self._store(lambda kv: kv.exists(f"gate:open:{provider}"), False):
            raise GateRejected("MODEL_CIRCUIT_OPEN")
        with self._slot(provider, limits):
            self._count_minute(provider, limits)
            try:
                result = fn()
            except Exception as exc:
                if is_failure(exc):
                    self._failed(provider, limits)
                raise
        self._store(lambda kv: kv.delete(f"gate:fail:{provider}"), 0)
        return result

    def _store(self, op: Callable[[KVClient], T], default: T) -> T:
        try:
            return op(self.kv)
        except ValkeyClientError as exc:
            log.warning("model gate store unavailable, letting call through: %s", exc.code)
            return default

    @contextmanager
    def _slot(self, provider: str, limits: GateLimits) -> Iterator[None]:
        if limits.concurrency <= 0:
            yield
            return
        token = uuid4().hex
        key = self._acquire(provider, limits, token)
        try:
            yield
        finally:
            if key:
                self._store(lambda kv: kv.delete_if(key, token), False)

    def _acquire(self, provider: str, limits: GateLimits, token: str) -> str | None:
        deadline = self.clock() + limits.wait_seconds
        while True:
            for i in range(limits.concurrency):
                key = f"gate:slot:{provider}:{i}"
                taken = self._store(
                    lambda kv, key=key: kv.set(
                        key, token, ttl=limits.lease_seconds, only_if_absent=True
                    ),
                    None,
                )
                if taken is None:
                    return None
                if taken:
                    return key
            if self.clock() >= deadline:
                log.warning("model gate busy: %s", provider)
                raise GateRejected("MODEL_BUSY")
            self.sleep(POLL_SECONDS)

    def _count_minute(self, provider: str, limits: GateLimits) -> None:
        if limits.per_minute <= 0:
            return
        key = f"gate:rpm:{provider}:{int(self.clock() // 60)}"
        if self._store(lambda kv: kv.incr(key, ttl=60), 0) > limits.per_minute:
            log.warning("model gate per-minute budget spent: %s", provider)
            raise GateRejected("MODEL_RATE_LIMITED")

    def _failed(self, provider: str, limits: GateLimits) -> None:
        if limits.breaker_failures <= 0:
            return
        key = f"gate:fail:{provider}"
        failures = self._store(lambda kv: kv.incr(key, ttl=limits.breaker_window_seconds), 0)
        if failures < limits.breaker_failures:
            return
        # Keep the count past the open period so one failed probe reopens the breaker.
        self._store(
            lambda kv: kv.set(f"gate:open:{provider}", "1", ttl=limits.breaker_open_seconds),
            False,
        )
        self._store(
            lambda kv: kv.expire(key, limits.breaker_open_seconds + limits.breaker_window_seconds),
            False,
        )
        log.warning("model gate breaker open: %s after %s failures", provider, failures)


def configured_limits(provider: str) -> GateLimits:
    return GateLimits(**config.model_gate_limits(provider))


@lru_cache
def model_gate() -> ModelGate:
    return ModelGate(
        build_kv_client(config.MODEL_GATE_STORE), configured_limits, config.MODEL_GATE == "on"
    )
