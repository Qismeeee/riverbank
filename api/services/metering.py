from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from threading import Lock
from uuid import uuid4

PRICES = Path(__file__).resolve().parents[1] / "studio" / "model_prices.yaml"


def parse_prices(text: str) -> dict[str, tuple[int, int]]:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, val = line.strip().partition(":")
        key = key.strip().strip('"').strip("'")
        val = val.strip().strip('"').strip("'")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError("model_prices")
        parent = stack[-1][1]
        if val == "":
            node: dict = {}
            parent[key] = node
            stack.append((indent, node))
        elif val.isdigit():
            parent[key] = int(val)
        else:
            raise ValueError(key)
    models = root.get("models")
    if not isinstance(models, dict):
        raise ValueError("models")
    prices: dict[str, tuple[int, int]] = {}
    for name, row in models.items():
        if (
            not isinstance(row, dict)
            or type(row.get("input")) is not int
            or type(row.get("output")) is not int
            or row["input"] < 0
            or row["output"] < 0
        ):
            raise ValueError(name)
        prices[name] = (row["input"], row["output"])
    return prices


@lru_cache(maxsize=1)
def load_prices() -> dict[str, tuple[int, int]]:
    return parse_prices(PRICES.read_text(encoding="utf-8"))


def token_count(usage: dict | None, name: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    value = usage.get(name)
    if type(value) is int and value >= 0:
        return value
    return None


def quote(
    model: str,
    prompt: int | None,
    completion: int | None,
    prices: dict[str, tuple[int, int]] | None = None,
) -> int | None:
    if type(prompt) is not int or type(completion) is not int:
        return None
    row = (prices if prices is not None else load_prices()).get(model)
    if row is None:
        return None
    return (prompt * row[0] + completion * row[1] + 500_000) // 1_000_000


@dataclass
class Bucket:
    case_id: str
    clock: Callable[[], datetime]
    run_id: str | None = None
    calls: list[dict] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)

    def append(self, call: dict) -> None:
        with self.lock:
            self.calls.append(call)

    def stamp(self, run_id: str) -> None:
        with self.lock:
            self.run_id = run_id
            for call in self.calls:
                if call["run_id"] is None:
                    call["case_id"] = self.case_id
                    call["run_id"] = run_id

    def snapshot(self, run_id: str) -> list[dict]:
        with self.lock:
            return [dict(call) for call in self.calls if call["run_id"] == run_id]


_bucket: ContextVar[Bucket | None] = ContextVar("model_calls", default=None)


def open_scope(case_id: str, clock: Callable[[], datetime]) -> Token:
    return _bucket.set(Bucket(case_id, clock))


def bind_run(
    case_id: str, run_id: str, clock: Callable[[], datetime]
) -> tuple[Token | None, Bucket]:
    current = _bucket.get()
    if current is None:
        bucket = Bucket(case_id, clock)
        token: Token | None = _bucket.set(bucket)
    else:
        bucket = current
        bucket.case_id = case_id
        token = None
    bucket.stamp(run_id)
    return token, bucket


def reset_meter(token: Token | None) -> None:
    if token is not None:
        _bucket.reset(token)


def record_usage(purpose: str, provider: str, model: str, usage: dict | None, cached: bool) -> None:
    bucket = _bucket.get()
    if bucket is None or not model:
        return
    prompt = token_count(usage, "prompt_tokens")
    completion = token_count(usage, "completion_tokens")
    bucket.append(
        {
            "call_id": str(uuid4()),
            "case_id": bucket.case_id,
            "run_id": bucket.run_id,
            "purpose": purpose,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": token_count(usage, "total_tokens"),
            "cached": bool(cached),
            "cost_micro_usd": quote(model, prompt, completion),
            "occurred_at": bucket.clock().isoformat(),
        }
    )
