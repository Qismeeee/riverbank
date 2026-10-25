from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import date, datetime, timedelta

from .evidence import digest
from .models import WorkflowError


class FeatureSource(ABC):
    @abstractmethod
    def fetch(self, seller_id: str, shop_id: str, now: datetime) -> tuple[dict, dict]: ...


class MappingFeatureSource(FeatureSource):
    def __init__(self, records: dict[tuple[str, str], tuple[dict, dict]]):
        self.records = deepcopy(records)

    def fetch(self, seller_id: str, shop_id: str, now: datetime) -> tuple[dict, dict]:
        record = self.records.get((seller_id, shop_id))
        if record is None:
            raise WorkflowError("SELLER_SHOP_SCOPE_NOT_FOUND", 403)
        consent, features = deepcopy(record)
        validate_consent(consent, now)
        return consent, features


def validate_consent(consent: dict, now: datetime) -> None:
    try:
        granted = datetime.fromisoformat(consent["granted_at"])
        expires = datetime.fromisoformat(consent["expires_at"])
        valid_time = granted.tzinfo is not None and expires.tzinfo is not None
        valid_time = valid_time and granted <= now < expires
    except (KeyError, TypeError, ValueError):
        valid_time = False
    if (
        consent.get("purpose") != "WORKING_CAPITAL_UNDERWRITING"
        or consent.get("revoked_at")
        or not consent.get("reference")
        or not valid_time
    ):
        raise WorkflowError("CONSENT_REQUIRED", 422)


class PostgresFeatureSource(FeatureSource):
    def __init__(self, database_url: str):
        self.database_url = database_url

    def fetch(self, seller_id: str, shop_id: str, now: datetime) -> tuple[dict, dict]:
        import psycopg

        with psycopg.connect(self.database_url) as conn:
            owner = conn.execute(
                "select 1 from seller_shops where seller_id=%s and shop_id=%s",
                (seller_id, shop_id),
            ).fetchone()
            if not owner:
                raise WorkflowError("SELLER_SHOP_SCOPE_NOT_FOUND", 403)
            row = conn.execute(
                "select payload from seller_consents where seller_id=%s and shop_id=%s "
                "order by recorded_at desc, consent_id desc limit 1",
                (seller_id, shop_id),
            ).fetchone()
            if not row:
                raise WorkflowError("CONSENT_REQUIRED", 422)
            consent = row[0]
            validate_consent(consent, now)
            row = conn.execute(
                "select payload,payload_hash,snapshot_id,captured_at "
                "from seller_feature_snapshots where seller_id=%s and shop_id=%s "
                "order by captured_at desc, snapshot_id desc limit 1",
                (seller_id, shop_id),
            ).fetchone()
            if not row:
                raise WorkflowError("PAYOUT_HISTORY_REQUIRED", 422)
            if digest(row[0]) != row[1]:
                raise WorkflowError("FEATURE_HASH_MISMATCH")
            features = row[0]
            features["snapshot_id"] = str(row[2])
            features["captured_at"] = row[3].isoformat()
            return consent, features


DEMO_PROFILES = {
    "fashion": {
        "archetype": "fashion",
        "shop_age_months": 12,
        "return_rate": 0.05,
        "n_products": 80,
        "top3_share": 0.3,
        "rating": 4.7,
    },
    "electronics": {
        "archetype": "electronics",
        "shop_age_months": 12,
        "return_rate": 0.05,
        "n_products": 40,
        "top3_share": 0.45,
        "rating": 4.8,
    },
    "fashion_young": {
        "archetype": "fashion",
        "shop_age_months": 7,
        "return_rate": 0.13,
        "n_products": 12,
        "top3_share": 0.8,
        "rating": 4.2,
    },
}


def demo_source(now: datetime) -> MappingFeatureSource:
    monday = now.date() - timedelta(days=now.weekday())
    records = {}
    for seller, payout, support, profile in (
        ("demo-a", 40_000_000, 90_000_000, DEMO_PROFILES["fashion"]),
        ("demo-b", 70_000_000, 90_000_000, DEMO_PROFILES["fashion"]),
        ("demo-c", 70_000_000, 30_000_000, DEMO_PROFILES["electronics"]),
        ("demo-d", 70_000_000, 90_000_000, DEMO_PROFILES["fashion_young"]),
    ):
        consent = {
            "reference": f"demo-consent-{seller}",
            "source": "synthetic",
            "purpose": "WORKING_CAPITAL_UNDERWRITING",
            "granted_at": (now - timedelta(days=1)).isoformat(),
            "expires_at": (now + timedelta(days=30)).isoformat(),
            "revoked_at": None,
        }
        features = {
            "seller_id": seller,
            "shop_id": seller,
            "source": "synthetic",
            "payout_origin": "simulated",
            "disclosure": "synthetic; payout mô phỏng",
            "platform_supported_amount": support,
            **profile,
            "weekly_payouts": [
                {"week_start": (monday - timedelta(weeks=12 - i)).isoformat(), "amount": payout}
                for i in range(12)
            ],
            "raw_refs": [],
            "etl_run_id": None,
        }
        records[(seller, seller)] = (consent, features)
    return MappingFeatureSource(records)


PROFILE_RANGES = {
    "shop_age_months": (int, 0, 1200),
    "n_products": (int, 0, 10**7),
    "top3_share": ((int, float), 0, 1),
    "return_rate": ((int, float), 0, 1),
    "rating": ((int, float), 0, 5),
}


def _valid_profile(features: dict) -> bool:
    archetype = features.get("archetype")
    if archetype is not None and (type(archetype) is not str or not archetype.strip()):
        return False
    for name, (kind, low, high) in PROFILE_RANGES.items():
        value = features.get(name)
        if value is None:
            continue
        if type(value) is bool or not isinstance(value, kind) or not low <= value <= high:
            return False
    return True


def validate_features(features: dict, seller_id: str, shop_id: str, now: datetime) -> None:
    allowed = {
        "seller_id",
        "shop_id",
        "source",
        "payout_origin",
        "disclosure",
        "platform_supported_amount",
        "shop_age_months",
        "return_rate",
        "archetype",
        "n_products",
        "top3_share",
        "rating",
        "weekly_payouts",
        "raw_refs",
        "etl_run_id",
        "source_snapshot",
        "simulation",
        "snapshot_id",
        "captured_at",
    }
    if set(features) - allowed:
        raise WorkflowError("UNAPPROVED_FEATURE_FIELDS", 422)
    if features.get("seller_id") != seller_id or features.get("shop_id") != shop_id:
        raise WorkflowError("FEATURE_SCOPE_MISMATCH", 403)
    if features.get("payout_origin") not in {"observed", "simulated"}:
        raise WorkflowError("PAYOUT_PROVENANCE_REQUIRED", 422)
    if (
        features.get("source") in {"addlivetag", "synthetic"}
        and features["payout_origin"] != "simulated"
    ):
        raise WorkflowError("SIMULATION_MUST_BE_LABELLED", 422)
    support = features.get("platform_supported_amount")
    if type(support) is not int or support < 0 or not features.get("disclosure"):
        raise WorkflowError("INVALID_FEATURE_SNAPSHOT", 422)
    if not _valid_profile(features):
        raise WorkflowError("INVALID_FEATURE_SNAPSHOT", 422)
    rows = features.get("weekly_payouts", [])
    if not 4 <= len(rows) <= 104:
        raise WorkflowError("PAYOUT_HISTORY_REQUIRED", 422)
    previous = None
    for row in rows:
        try:
            start = date.fromisoformat(row["week_start"])
        except (KeyError, ValueError) as exc:
            raise WorkflowError("INVALID_PAYOUT_PERIOD", 422) from exc
        if (
            start.weekday() != 0
            or start + timedelta(days=7) > now.date()
            or (previous and start != previous + timedelta(days=7))
            or type(row.get("amount")) is not int
            or row["amount"] < 0
        ):
            raise WorkflowError("INVALID_PAYOUT_HISTORY", 422)
        previous = start


class CicSource(ABC):
    name: str

    @abstractmethod
    def inquire(self, seller_id: str, now: datetime) -> dict: ...

    def describe(self) -> dict:
        return {"source": self.name}


class DemoCicSource(CicSource):
    name = "mock"

    def inquire(self, seller_id: str, now: datetime) -> dict:
        return {
            "seller_id": seller_id,
            "source": "mock",
            "bureau_group": 1,
            "outstanding": 0,
            "queried_at": now.isoformat(),
        }


class LosAdapter(ABC):
    @abstractmethod
    def send(self, payload: dict, idempotency_key: str) -> str: ...


class MockLosAdapter(LosAdapter):
    def send(self, payload: dict, idempotency_key: str) -> str:
        return f"MOCK-{idempotency_key}"
