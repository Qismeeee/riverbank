from services.guardrail import GuardUnavailable, InputGuard, jev_block_details
from services.underwriting.models import WorkflowError


def screen(guard: InputGuard, text: str) -> None:
    try:
        verdict = guard.check(text)
    except GuardUnavailable as e:
        raise WorkflowError("GUARD_UNAVAILABLE", 503) from e
    if not verdict.blocked:
        return
    details = jev_block_details(verdict.flags, verdict.threshold)
    known = {item["flag"] for item in details}
    details.extend(
        {"flag": flag, "probability": probability, "threshold": verdict.threshold}
        for flag, probability in verdict.flags.items()
        if probability >= verdict.threshold and flag not in known
    )
    raise WorkflowError("BLOCKED_INPUT", 422, details)
