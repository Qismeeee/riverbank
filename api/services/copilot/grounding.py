import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .models import Extraction

UNITS = {
    "tỷ": 10**9,
    "ty": 10**9,
    "triệu": 10**6,
    "trieu": 10**6,
    "tr": 10**6,
    "nghìn": 10**3,
    "nghin": 10**3,
    "k": 10**3,
    "vnd": 1,
    "đ": 1,
}
_TOKEN = re.compile(
    r"(?<![\w.,])(\d+(?:[.,]\d+)*)\s*("
    + "|".join(sorted(map(re.escape, UNITS), key=len, reverse=True))
    + r")?(?!\w)",
    re.IGNORECASE,
)
PURCHASE_WEEK = 0


@dataclass(frozen=True)
class Mentions:
    amounts: frozenset[int]
    counts: frozenset[int]

    @classmethod
    def parse(cls, text: str) -> "Mentions":
        amounts: set[int] = set()
        counts: set[int] = set()
        for raw, unit in _TOKEN.findall(text):
            scale = UNITS[unit.lower()] if unit else 1
            for value in _readings(raw):
                if (value * scale) == (value * scale).to_integral_value():
                    amounts.add(int(value * scale))
                if not unit and value == value.to_integral_value():
                    counts.add(int(value))
        return cls(frozenset(amounts), frozenset(counts))


def _readings(raw: str) -> set[Decimal]:
    head, *groups = re.split(r"[.,]", raw)
    candidates = set()
    if all(len(g) == 3 for g in groups):
        candidates.add(head + "".join(groups))
    if len(groups) == 1:
        candidates.add(f"{head}.{groups[0]}")
    readings = set()
    for candidate in candidates:
        try:
            readings.add(Decimal(candidate))
        except InvalidOperation:
            continue
    return readings


class PlanGrounder:
    money_fields = ("lot_value", "cash_on_hand", "requested_amount")
    count_fields = ("sell_weeks",)

    def ground(self, extraction: Extraction, text: str) -> dict:
        mentions = Mentions.parse(text)
        values = extraction.model_dump(include={*self.money_fields, *self.count_fields})
        for name in self.money_fields:
            if values[name] not in mentions.amounts:
                values[name] = None
        for name in self.count_fields:
            if values[name] not in mentions.counts:
                values[name] = None
        values["supplier_schedule"] = self._schedule(extraction, values["lot_value"], mentions)
        return values

    @staticmethod
    def _schedule(extraction: Extraction, lot_value: int | None, mentions: Mentions):
        rows = extraction.supplier_schedule
        if not rows or lot_value is None or sum(r.amount for r in rows) != lot_value:
            return None
        weeks = mentions.counts | {PURCHASE_WEEK}
        if any(r.week not in weeks for r in rows):
            return None
        return [r.model_dump() for r in rows]
