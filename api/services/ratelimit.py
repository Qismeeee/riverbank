import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock

from services.underwriting.models import WorkflowError


@dataclass(frozen=True)
class RateLimit:
    max_hits: int
    window_seconds: int

    @classmethod
    def parse(cls, text: str) -> "RateLimit":
        hits, seconds = text.split("/")
        return cls(int(hits), int(seconds))

    @property
    def enabled(self) -> bool:
        return self.max_hits > 0 and self.window_seconds > 0


class SlidingWindow:
    def __init__(self, max_hits: int, window_seconds: int):
        self.max_hits, self.window = max_hits, timedelta(seconds=window_seconds)
        self._hits: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = Lock()

    def _recent(self, key: str, now: datetime) -> deque[datetime]:
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if not hits:
            del self._hits[key]
            return deque()
        return hits

    def _full(self, key: str, now: datetime) -> bool:
        return len(self._recent(key, now)) >= self.max_hits

    def _retry_after(self, key: str, now: datetime) -> int:
        oldest = self._hits[key][0]
        return max(1, math.ceil((oldest + self.window - now).total_seconds()))

    def _add(self, key: str, now: datetime) -> None:
        self._hits[key].append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


class RateLimiter(SlidingWindow):
    def __init__(self, limit: RateLimit):
        super().__init__(limit.max_hits, limit.window_seconds)
        self.enabled = limit.enabled

    def hit(self, key: str, now: datetime) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._full(key, now):
                retry = self._retry_after(key, now)
                raise WorkflowError(
                    "RATE_LIMITED",
                    429,
                    [{"retry_after_seconds": retry, "limit": self.max_hits}],
                    {"Retry-After": str(retry)},
                )
            self._add(key, now)
