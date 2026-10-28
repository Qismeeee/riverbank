import pytest
from valkey.exceptions import ConnectionError as ValkeyConnectionError

from kv import (
    MemoryKVClient,
    ValkeyClient,
    ValkeyClientError,
    ValkeySettings,
    build_kv_client,
    valkey_settings,
)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakePipeline:
    def __init__(self, client):
        self.client, self.ops = client, []

    def incrby(self, key, amount):
        self.ops.append(("incrby", key, amount))

    def expire(self, key, ttl, nx=False):
        self.ops.append(("expire", key, ttl, nx))

    def execute(self):
        self.client.calls.extend(self.ops)
        return [7] + [True] * (len(self.ops) - 1)


class FakeValkey:
    def __init__(self, fail=False):
        self.calls, self.fail, self.closed = [], fail, False
        self.data = {}

    def get(self, key):
        if self.fail:
            raise ValkeyConnectionError("down")
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False):
        self.calls.append(("set", key, value, ex, nx))
        self.data[key] = value
        return True

    def delete(self, *keys):
        self.calls.append(("delete", *keys))
        return len(keys)

    def eval(self, script, numkeys, *args):
        self.calls.append(("eval", numkeys, *args))
        return 1

    def pipeline(self):
        return FakePipeline(self)

    def close(self):
        self.closed = True


def test_memory_set_get_delete():
    kv = MemoryKVClient()
    assert kv.set("a", "1")
    assert kv.get("a") == "1"
    assert kv.exists("a")
    assert kv.delete("a", "missing") == 1
    assert kv.get("a") is None


def test_memory_ttl_expires():
    clock = Clock()
    kv = MemoryKVClient(clock)
    kv.set("a", "1", ttl=10)
    assert kv.ttl("a") == 10
    clock.now += 10
    assert kv.get("a") is None
    assert kv.ttl("a") == -2


def test_memory_only_if_absent():
    kv = MemoryKVClient()
    assert kv.set("lock", "x", only_if_absent=True)
    assert not kv.set("lock", "y", only_if_absent=True)
    assert kv.get("lock") == "x"


def test_memory_incr_keeps_first_ttl():
    clock = Clock()
    kv = MemoryKVClient(clock)
    assert kv.incr("hits", ttl=60) == 1
    clock.now += 30
    assert kv.incr("hits", ttl=60) == 2
    assert kv.ttl("hits") == 30
    kv.set("name", "abc")
    with pytest.raises(ValkeyClientError, match="VALKEY_NOT_INTEGER"):
        kv.incr("name")


def test_memory_json_roundtrip():
    kv = MemoryKVClient()
    kv.set_json("case", {"shop": "Cửa hàng", "gap": 80_000_000})
    assert kv.get_json("case") == {"shop": "Cửa hàng", "gap": 80_000_000}
    kv.set("bad", "{")
    with pytest.raises(ValkeyClientError, match="VALKEY_INVALID_VALUE"):
        kv.get_json("bad")


def test_valkey_client_prefixes_keys():
    fake = FakeValkey()
    kv = ValkeyClient(ValkeySettings("localhost", key_prefix="rb:"), client=fake)
    kv.set("a", "1", ttl=5, only_if_absent=True)
    kv.delete("a", "b")
    assert kv.get("a") == "1"
    assert fake.calls == [("set", "rb:a", "1", 5, True), ("delete", "rb:a", "rb:b")]


def test_delete_if_only_removes_matching_value():
    kv = MemoryKVClient()
    kv.set("slot", "mine")
    assert not kv.delete_if("slot", "other")
    assert kv.delete_if("slot", "mine")
    assert kv.get("slot") is None
    fake = FakeValkey()
    assert ValkeyClient(ValkeySettings("localhost", key_prefix="rb:"), client=fake).delete_if(
        "slot", "mine"
    )
    assert fake.calls == [("eval", 1, "rb:slot", "mine")]


def test_valkey_incr_sets_ttl_only_once():
    fake = FakeValkey()
    kv = ValkeyClient(ValkeySettings("localhost", key_prefix=""), client=fake)
    assert kv.incr("hits", ttl=60) == 7
    assert fake.calls == [("incrby", "hits", 1), ("expire", "hits", 60, True)]


def test_valkey_errors_are_wrapped():
    kv = ValkeyClient(ValkeySettings("localhost"), client=FakeValkey(fail=True))
    with pytest.raises(ValkeyClientError, match="VALKEY_UNAVAILABLE"):
        kv.get("a")


def test_valkey_close_releases_client():
    fake = FakeValkey()
    kv = ValkeyClient(ValkeySettings("localhost"), client=fake)
    kv.close()
    assert fake.closed


def test_settings_connection_options():
    options = ValkeySettings("cache", 6380, 2, "app", "secret", ssl=True).connection()
    assert options["host"] == "cache" and options["port"] == 6380 and options["db"] == 2
    assert options["username"] == "app" and options["password"] == "secret"
    assert options["ssl"] and options["decode_responses"]
    assert "password" not in ValkeySettings("cache").connection()


def test_settings_require_host():
    with pytest.raises(ValkeyClientError, match="VALKEY_NOT_CONFIGURED"):
        ValkeySettings("").connection()


def test_settings_read_from_config(monkeypatch):
    monkeypatch.setattr("config.VALKEY_HOST", "valkey.internal")
    monkeypatch.setattr("config.VALKEY_PORT", 6390)
    assert valkey_settings().host == "valkey.internal"
    assert valkey_settings().port == 6390


def test_build_kv_client():
    assert isinstance(build_kv_client("memory"), MemoryKVClient)
    assert isinstance(build_kv_client("valkey"), ValkeyClient)
    with pytest.raises(ValkeyClientError, match="KV_STORE_UNKNOWN"):
        build_kv_client("etcd")
