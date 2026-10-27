import json
from datetime import UTC, datetime, timedelta

import pytest

from services.underwriting.models import WorkflowError
from services.underwriting.sources import HISTORY_WEEKS, SellerBook, seller_book_source

NOW = datetime(2026, 10, 31, 2, 0, tzinfo=UTC)
PROFILES = {"shop": {"archetype": "fashion", "shop_age_months": 12, "rating": 4.7}}


def seller(**overrides) -> dict:
    return {
        "seller_id": "s1",
        "profile": "shop",
        "platform_supported_amount": 50_000_000,
        "weekly_payouts": [40_000_000] * HISTORY_WEEKS,
        **overrides,
    }


def write(tmp_path, sellers: list[dict], profiles: dict = PROFILES):
    path = tmp_path / "sellers.json"
    path.write_text(json.dumps({"profiles": profiles, "sellers": sellers}), encoding="utf-8")
    return path


def test_shipped_book_loads_every_seller():
    ids = [s.seller_id for s in SellerBook.load().sellers]
    assert len(ids) == len(set(ids))
    assert {"demo-a", "demo-d", "demo-e", "demo-j"} <= set(ids)


def test_records_end_on_week_before_current_monday(tmp_path):
    book = SellerBook.load(write(tmp_path, [seller()]))
    consent, features = seller_book_source(NOW, book).fetch("s1", "s1", NOW)
    weeks = [w["week_start"] for w in features["weekly_payouts"]]
    monday = NOW.date() - timedelta(days=NOW.weekday())
    assert len(weeks) == HISTORY_WEEKS
    assert weeks[-1] == (monday - timedelta(weeks=1)).isoformat()
    assert features["archetype"] == "fashion"
    assert consent["reference"] == "consent-s1"


@pytest.mark.parametrize(
    "bad",
    [
        seller(weekly_payouts=[1] * (HISTORY_WEEKS - 1)),
        seller(weekly_payouts=[-1] + [1] * (HISTORY_WEEKS - 1)),
        seller(weekly_payouts=[1.5] * HISTORY_WEEKS),
        seller(platform_supported_amount=-1),
        seller(profile="missing"),
    ],
)
def test_invalid_seller_rejected(tmp_path, bad):
    with pytest.raises(WorkflowError) as err:
        SellerBook.load(write(tmp_path, [bad]))
    assert err.value.code == "SELLER_BOOK_INVALID"


def test_invalid_profile_rejected(tmp_path):
    profiles = {"shop": {"archetype": "fashion", "rating": 9}}
    with pytest.raises(WorkflowError):
        SellerBook.load(write(tmp_path, [seller()], profiles))


def test_missing_file_rejected(tmp_path):
    with pytest.raises(WorkflowError):
        SellerBook.load(tmp_path / "nope.json")
