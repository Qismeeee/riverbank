import json
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from threading import Lock

import config

from .config import ValkeyClientError, ValkeySettings, valkey_settings

DELETE_IF_EQUAL = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"
)


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def decode(raw: str | None):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ValkeyClientError("VALKEY_INVALID_VALUE") from exc


class KVClient(ABC):
    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(
        self, key: str, value: str, ttl: int | None = None, only_if_absent: bool = False
    ) -> bool: ...

    @abstractmethod
    def delete(self, *keys: str) -> int: ...

    @abstractmethod
    def delete_if(self, key: str, value: str) -> bool: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def incr(self, key: str, amount: int = 1, ttl: int | None = None) -> int: ...

    @abstractmethod
    def expire(self, key: str, ttl: int) -> bool: ...

    @abstractmethod
    def ttl(self, key: str) -> int: ...

    @abstractmethod
    def ping(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...

    def get_json(self, key: str):
        return decode(self.get(key))

    def set_json(self, key: str, value, ttl: int | None = None, only_if_absent: bool = False):
        return self.set(key, encode(value), ttl, only_if_absent)


class ValkeyClient(KVClient):
    def __init__(self, settings: ValkeySettings, client=None):
        self.settings = settings
        self._client = client

    def _get_client(self):
        if self._client is None:
            from valkey import Valkey

            self._client = Valkey(**self.settings.connection())
        return self._client

    def _key(self, key: str) -> str:
        return self.settings.key_prefix + key

    def _call(self, fn: Callable):
        from valkey.exceptions import ValkeyError

        try:
            return fn(self._get_client())
        except ValkeyError as exc:
            raise ValkeyClientError("VALKEY_UNAVAILABLE") from exc

    def get(self, key: str) -> str | None:
        return self._call(lambda c: c.get(self._key(key)))

    def set(
        self, key: str, value: str, ttl: int | None = None, only_if_absent: bool = False
    ) -> bool:
        return bool(self._call(lambda c: c.set(self._key(key), value, ex=ttl, nx=only_if_absent)))

    def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return self._call(lambda c: c.delete(*(self._key(k) for k in keys)))

    def delete_if(self, key: str, value: str) -> bool:
        return bool(self._call(lambda c: c.eval(DELETE_IF_EQUAL, 1, self._key(key), value)))

    def exists(self, key: str) -> bool:
        return bool(self._call(lambda c: c.exists(self._key(key))))

    def incr(self, key: str, amount: int = 1, ttl: int | None = None) -> int:
        def run(c):
            pipe = c.pipeline()
            pipe.incrby(self._key(key), amount)
            if ttl is not None:
                pipe.expire(self._key(key), ttl, nx=True)
            return pipe.execute()[0]

        return self._call(run)

    def expire(self, key: str, ttl: int) -> bool:
        return bool(self._call(lambda c: c.expire(self._key(key), ttl)))

    def ttl(self, key: str) -> int:
        return self._call(lambda c: c.ttl(self._key(key)))

    def ping(self) -> bool:
        return bool(self._call(lambda c: c.ping()))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


class MemoryKVClient(KVClient):
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._data: dict[str, tuple[str, float | None]] = {}
        self._lock = Lock()

    def _live(self, key: str) -> tuple[str, float | None] | None:
        entry = self._data.get(key)
        if entry is not None and entry[1] is not None and entry[1] <= self._clock():
            del self._data[key]
            return None
        return entry

    def _deadline(self, ttl: int | None) -> float | None:
        return None if ttl is None else self._clock() + ttl

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._live(key)
            return entry[0] if entry else None

    def set(
        self, key: str, value: str, ttl: int | None = None, only_if_absent: bool = False
    ) -> bool:
        with self._lock:
            if only_if_absent and self._live(key) is not None:
                return False
            self._data[key] = (str(value), self._deadline(ttl))
            return True

    def delete(self, *keys: str) -> int:
        with self._lock:
            removed = [k for k in keys if self._live(k) is not None]
            for k in removed:
                del self._data[k]
            return len(removed)

    def delete_if(self, key: str, value: str) -> bool:
        with self._lock:
            entry = self._live(key)
            if entry is None or entry[0] != value:
                return False
            del self._data[key]
            return True

    def exists(self, key: str) -> bool:
        with self._lock:
            return self._live(key) is not None

    def incr(self, key: str, amount: int = 1, ttl: int | None = None) -> int:
        with self._lock:
            entry = self._live(key)
            try:
                current = int(entry[0]) if entry else 0
            except ValueError as exc:
                raise ValkeyClientError("VALKEY_NOT_INTEGER") from exc
            deadline = entry[1] if entry and entry[1] is not None else self._deadline(ttl)
            self._data[key] = (str(current + amount), deadline)
            return current + amount

    def expire(self, key: str, ttl: int) -> bool:
        with self._lock:
            entry = self._live(key)
            if entry is None:
                return False
            self._data[key] = (entry[0], self._deadline(ttl))
            return True

    def ttl(self, key: str) -> int:
        with self._lock:
            entry = self._live(key)
            if entry is None:
                return -2
            if entry[1] is None:
                return -1
            return max(0, int(entry[1] - self._clock()))

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None


CLIENTS: dict[str, Callable[[], KVClient]] = {
    "memory": MemoryKVClient,
    "valkey": lambda: ValkeyClient(valkey_settings()),
}


def build_kv_client(kind: str | None = None) -> KVClient:
    kind = kind or config.KV_STORE
    if kind not in CLIENTS:
        raise ValkeyClientError("KV_STORE_UNKNOWN")
    return CLIENTS[kind]()


if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    client = build_kv_client("valkey")
    client.set_json(key, {"event": "smoke", "source": "kv.client"}, ttl=60)
    print(client.get_json(key), client.ttl(key))
    client.close()
