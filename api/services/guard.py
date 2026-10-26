import re
from decimal import Decimal
from pathlib import Path

_NUMBER = re.compile(r"\d{1,3}(?:\.\d{3})+|\d+")
_APPROVED = ("đã duyệt", "được duyệt", "accepted")
_REJECTED = ("đã từ chối", "rejected")
_NOT_YET = ("chưa được duyệt",)
_NO_LOAN = ("không cần vay", "chưa cần vay")
_SCALED = re.compile(r"(?<![\d.,])(\d+)(?:[.,](\d{1,2}))?\s*(triệu|tỷ)(?!\w)", re.IGNORECASE)
_SCALES = {"triệu": 10**6, "tỷ": 10**9}


def numbers(text: str) -> list[int]:
    return [int(match.group().replace(".", "")) for match in _NUMBER.finditer(text)]


def _expand(match: re.Match) -> str:
    whole, fraction, unit = match.groups()
    value = Decimal(f"{whole}.{fraction or 0}") * _SCALES[unit.casefold()]
    return str(int(value)) if value == value.to_integral_value() else match.group()


def expand_units(text: str) -> str:
    return _SCALED.sub(_expand, text)


def in_order(text: str, reference: str) -> bool:
    expected = list(dict.fromkeys(numbers(reference)))
    seen = list(dict.fromkeys(n for n in numbers(text) if n in expected))
    return seen == [n for n in expected if n in seen]


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
        for phrase in _NOT_YET:
            folded = folded.replace(phrase, "")
        if status != "ACCEPTED" and any(token in folded for token in _APPROVED):
            return True
        if status != "REJECTED" and any(token in folded for token in _REJECTED):
            return True
        no_loan = any(phrase in folded for phrase in _NO_LOAN)
        if no_loan and status != "NO_LOAN_NEEDED":
            return True
        needs_loan = "cần vay" in folded and not no_loan
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
