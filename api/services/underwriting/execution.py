from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from services.metering import snapshot_run_calls

from .evidence import digest
from .models import Case, WorkflowError


@dataclass
class Execution:
    case: Case
    run_id: str
    clock: Callable[[], datetime]
    checkpoint: Callable[[], None] | None = None
    spans: list[dict] = field(default_factory=list)

    def step(
        self,
        name: str,
        inputs: Any,
        action: Callable,
        repeat_safe: bool = True,
        persist: bool = True,
    ) -> Any:
        save = self.checkpoint if persist else None
        steps = self.case.progress.setdefault("steps", {}) if save else {}
        saved = steps.get(name, {})
        if saved.get("state") == "SUCCEEDED":
            if saved["input_hash"] != digest(inputs):
                raise WorkflowError("CHECKPOINT_INPUT_CHANGED", 409)
            self.spans.append({**saved["span"], "cached": True})
            return deepcopy(saved["result"])
        if saved.get("state") == "STARTED" and not repeat_safe:
            raise WorkflowError("CIC_RECONCILIATION_REQUIRED", 503)
        started = self.clock()
        span = {
            "span_id": str(uuid4()),
            "trace_id": self.run_id,
            "step": name,
            "input_hash": digest(inputs),
            "started_at": started.isoformat(),
            "attempt": saved.get("attempt", 0) + 1,
            "cached": False,
        }
        if save:
            steps[name] = {"state": "STARTED", "attempt": span["attempt"]}
            self.case.progress["model_calls"] = snapshot_run_calls(self.run_id)
            save()
        try:
            result = action()
        except BaseException as exc:
            span["error_code"] = exc.code if isinstance(exc, WorkflowError) else type(exc).__name__
            span["duration_ms"] = (self.clock() - started) // timedelta(milliseconds=1)
            self.spans.append(span)
            if self.checkpoint:
                self.case.progress["last_error"] = span
                self.case.progress["model_calls"] = snapshot_run_calls(self.run_id)
                self.checkpoint()
            raise
        output = asdict(result) if is_dataclass(result) else result
        span.update(
            output_hash=digest(output),
            duration_ms=(self.clock() - started) // timedelta(milliseconds=1),
            error_code=None,
        )
        self.spans.append(span)
        if save:
            steps[name] = {
                "state": "SUCCEEDED",
                "input_hash": digest(inputs),
                "result": deepcopy(result),
                "span": span,
                "attempt": span["attempt"],
            }
            self.case.progress["model_calls"] = snapshot_run_calls(self.run_id)
            save()
        return result


_execution: ContextVar[Execution | None] = ContextVar("underwriting_execution", default=None)
_checkpoint: ContextVar[Callable[[], None] | None] = ContextVar("initial_checkpoint", default=None)


@contextmanager
def initial_checkpoint(save: Callable[[], None]) -> Iterator[None]:
    token = _checkpoint.set(save)
    try:
        yield
    finally:
        _checkpoint.reset(token)


def checkpoint_active() -> bool:
    return _checkpoint.get() is not None


@contextmanager
def execution(case: Case, run_id: str, clock: Callable[[], datetime]) -> Iterator[Execution]:
    current = Execution(case, run_id, clock, _checkpoint.get())
    token = _execution.set(current)
    try:
        yield current
    finally:
        _execution.reset(token)


def step(
    name: str, inputs: Any, action: Callable, repeat_safe: bool = True, persist: bool = True
) -> Any:
    current = _execution.get()
    return current.step(name, inputs, action, repeat_safe, persist) if current else action()
