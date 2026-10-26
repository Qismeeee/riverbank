from services.guardrail import GuardUnavailable, InputGuard
from services.underwriting.models import WorkflowError


def screen(guard: InputGuard, text: str) -> None:
    try:
        verdict = guard.check(text)
    except GuardUnavailable as e:
        raise WorkflowError("GUARD_UNAVAILABLE", 503) from e
    if verdict.blocked:
        raise WorkflowError(
            "BLOCKED_INPUT",
            422,
            [
                {"flag": flag, "probability": p, "threshold": verdict.threshold}
                for flag, p in verdict.flags.items()
                if p >= verdict.threshold
            ],
        )
