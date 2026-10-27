from datetime import UTC, datetime

import pytest

from services.shopee.schemas import EscrowDetail, ShopData
from services.shopee.source import ShopeeSource
from services.underwriting.models import WorkflowError
from services.underwriting.shopee_features import ShopeeFeatureSource, ShopeeRegistration
from services.underwriting.sources import seller_book_source, validate_features

NOW = datetime(2026, 10, 22, tzinfo=UTC)


class FakeShopeeSource(ShopeeSource):
    def __init__(self):
        self.calls = []

    def fetch(self, time_from: int, time_to: int) -> ShopData:
        self.calls.append((time_from, time_to))
        return ShopData(
            [],
            [
                EscrowDetail(
                    "order",
                    70_000_000,
                    75_000_000,
                    escrow_release_time=int(datetime(2026, 10, 13, tzinfo=UTC).timestamp()),
                )
            ],
            [],
        )


def test_shopee_adapter_uses_release_time_and_completed_weeks():
    source = FakeShopeeSource()
    consent = seller_book_source(NOW).records[("demo-b", "demo-b")][0]
    adapter = ShopeeFeatureSource(
        {
            ("demo-b", "shop"): ShopeeRegistration(
                source,
                "seller_export",
                consent,
                90_000_000,
                ("local:anonymized-export",),
            )
        }
    )
    _, features = adapter.fetch("demo-b", "shop", NOW)
    validate_features(features, "demo-b", "shop", NOW)
    assert features["payout_origin"] == "observed"
    assert features["weekly_payouts"][-1] == {"week_start": "2026-10-12", "amount": 70_000_000}
    assert sum(w["amount"] for w in features["weekly_payouts"]) == 70_000_000
    assert features["shop_age_months"] is None
    assert len(source.calls) == 1


def test_shopee_adapter_checks_consent_before_fetch():
    source = FakeShopeeSource()
    consent = seller_book_source(NOW).records[("demo-b", "demo-b")][0]
    consent["revoked_at"] = NOW.isoformat()
    adapter = ShopeeFeatureSource(
        {
            ("demo-b", "shop"): ShopeeRegistration(
                source,
                "seller_export",
                consent,
                90_000_000,
                (),
            )
        }
    )
    with pytest.raises(WorkflowError, match="CONSENT_REQUIRED"):
        adapter.fetch("demo-b", "shop", NOW)
    assert not source.calls
