import re
from pathlib import Path

_NUMBER = re.compile(r"\d{1,3}(?:\.\d{3})+|\d+")
_APPROVED = (
    "đã duyệt",
    "được duyệt",
    "đã chấp thuận",
    "được chấp thuận",
    "đã phê duyệt",
    "được phê duyệt",
    "accepted",
    "approved",
)
_REJECTED = ("đã từ chối", "rejected")


def numbers(text: str) -> list[int]:
    return [int(match.group().replace(".", "")) for match in _NUMBER.finditer(text)]


def ints(node: object) -> set[int]:
    if isinstance(node, bool) or node is None:
        return set()
    if isinstance(node, int):
        return {node}
    if isinstance(node, float):
        return {int(node)} if node.is_integer() else set()
    if isinstance(node, str):
        return set(numbers(node))
    if isinstance(node, dict):
        node = list(node.values())
    if isinstance(node, list):
        return set().union(*(ints(value) for value in node))
    return set()


def forbidden_words(guardrails: Path) -> tuple[str, ...]:
    text = guardrails.read_text(encoding="utf-8")
    section = text.split("### Từ cấm", 1)[1].split("###", 1)[0]
    return tuple(word.casefold() for word in re.findall(r"`([^`]+)`", section))


class OutputGuard:
    def __init__(self, forbidden: tuple[str, ...]):
        self.forbidden = forbidden

    @classmethod
    def from_guardrails(cls, guardrails: Path) -> "OutputGuard":
        return cls(forbidden_words(guardrails))

    def lexicon_hits(self, text: str) -> list[str]:
        folded = text.casefold()
        return [word for word in self.forbidden if word in folded]

    @staticmethod
    def contradicts(text: str, status: str) -> bool:
        folded = text.casefold()
        if status != "ACCEPTED" and any(token in folded for token in _APPROVED):
            return True
        if status != "REJECTED" and any(token in folded for token in _REJECTED):
            return True
        if "không cần vay" in folded and status != "NO_LOAN_NEEDED":
            return True
        needs_loan = "cần vay" in folded and "không cần vay" not in folded
        return needs_loan and status == "NO_LOAN_NEEDED"

    def check(self, text: str, allowed: set[int] | None, status: str) -> list[str]:
        reasons = []
        if self.lexicon_hits(text):
            reasons.append("lexicon")
        found = set(numbers(text))
        if found and allowed is None:
            reasons.append("missing_evidence")
        elif not found <= (allowed or set()):
            reasons.append("number")
        if self.contradicts(text, status):
            reasons.append("contradicts_status")
        return reasons
