import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .evidence import digest
from .models import WorkflowError
from .sources import CicSource

PROFILES_PATH = Path(__file__).resolve().parents[2] / "data" / "cic_profiles.json"
DEBT_GROUPS = range(1, 6)
HISTORY_MONTHS = 12
NO_HISTORY_GROUP = 1


@dataclass(frozen=True)
class CreditFacility:
    institution: str
    product: str
    limit: int
    outstanding: int
    group: int
    opened_months_ago: int
    history: tuple[int, ...]

    @classmethod
    def parse(cls, raw: dict) -> "CreditFacility":
        history = raw.get("history") or [raw["group"]] * HISTORY_MONTHS
        facility = cls(
            raw["institution"],
            raw["product"],
            raw["limit"],
            raw["outstanding"],
            raw["group"],
            raw["opened_months_ago"],
            tuple(history),
        )
        facility._validate()
        return facility

    def _validate(self) -> None:
        amounts = (self.limit, self.outstanding, self.opened_months_ago)
        if not all(type(v) is int and v >= 0 for v in amounts):
            raise ValueError("facility amounts must be non-negative integers")
        if len(self.history) != HISTORY_MONTHS or self.history[-1] != self.group:
            raise ValueError("history must hold 12 months ending at the current group")
        if any(type(g) is not int or g not in DEBT_GROUPS for g in self.history):
            raise ValueError("debt group must be 1 to 5")

    def payload(self, now: datetime) -> dict:
        months = now.year * 12 + now.month - 1 - self.opened_months_ago
        return {
            "institution": self.institution,
            "product": self.product,
            "limit": self.limit,
            "outstanding": self.outstanding,
            "group": self.group,
            "opened_month": f"{months // 12:04d}-{months % 12 + 1:02d}",
            "history_12m": list(self.history),
        }


@dataclass(frozen=True)
class CicProfile:
    facilities: tuple[CreditFacility, ...]
    recent_inquiries_90d: int

    @classmethod
    def parse(cls, raw: dict) -> "CicProfile":
        inquiries = raw.get("recent_inquiries_90d", 0)
        if type(inquiries) is not int or inquiries < 0:
            raise ValueError("recent_inquiries_90d must be a non-negative integer")
        return cls(tuple(CreditFacility.parse(f) for f in raw["facilities"]), inquiries)


class CicProfileBook:
    def __init__(self, default: CicProfile, sellers: dict[str, CicProfile], profiles_hash: str):
        self.default, self.sellers, self.profiles_hash = default, dict(sellers), profiles_hash

    @classmethod
    def load(cls, path: Path = PROFILES_PATH) -> "CicProfileBook":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                CicProfile.parse(raw["default"]),
                {seller: CicProfile.parse(p) for seller, p in raw["sellers"].items()},
                digest(raw),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise WorkflowError("CIC_PROFILES_INVALID", 503, [type(exc).__name__]) from exc

    def get(self, seller_id: str) -> CicProfile:
        return self.sellers.get(seller_id, self.default)


class ProfileCicSource(CicSource):
    name = "cic_profiles"

    def __init__(self, book: CicProfileBook | None = None):
        self.book = book or CicProfileBook.load()

    def inquire(self, seller_id: str, now: datetime) -> dict:
        profile = self.book.get(seller_id)
        facilities = profile.facilities
        return {
            "seller_id": seller_id,
            "source": self.name,
            "profiles_hash": self.book.profiles_hash,
            "bureau_group": max((f.group for f in facilities), default=NO_HISTORY_GROUP),
            "outstanding": sum(f.outstanding for f in facilities),
            "worst_group_12m": max(
                (g for f in facilities for g in f.history), default=NO_HISTORY_GROUP
            ),
            "has_credit_history": bool(facilities),
            "recent_inquiries_90d": profile.recent_inquiries_90d,
            "facilities": [f.payload(now) for f in facilities],
            "queried_at": now.isoformat(),
        }

    def describe(self) -> dict:
        return {
            "source": self.name,
            "profiles": len(self.book.sellers),
            "profiles_hash": self.book.profiles_hash,
        }
