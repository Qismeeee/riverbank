import json
from datetime import UTC, datetime

import pytest

from routers import underwriting as routes
from services.underwriting.cic import (
    HISTORY_MONTHS,
    NO_HISTORY_GROUP,
    CicProfileBook,
    SimulatedCicSource,
)
from services.underwriting.evidence import read_evidence
from services.underwriting.models import Plan, WorkflowError
from services.underwriting.repository import MemoryCaseRepository
from services.underwriting.risk import DemoRiskModel
from services.underwriting.service import UnderwritingService
from services.underwriting.sources import MockLosAdapter, demo_source
from tests.test_underwriting import NOW, plan


def facility(**changes):
    raw = {
        "institution": "Ngân hàng X",
        "product": "Vay tiêu dùng",
        "limit": 50_000_000,
        "outstanding": 10_000_000,
        "group": 1,
        "opened_months_ago": 3,
    }
    raw.update(changes)
    return raw


def book_file(tmp_path, sellers, default=None):
    path = tmp_path / "cic.json"
    path.write_text(
        json.dumps({"default": default or {"facilities": []}, "sellers": sellers}),
        encoding="utf-8",
    )
    return path


def service_with(book):
    return UnderwritingService(
        MemoryCaseRepository(),
        demo_source(NOW),
        DemoRiskModel(),
        SimulatedCicSource(book),
        MockLosAdapter(),
        clock=lambda: NOW,
    )


def test_shipped_profiles_cover_every_demo_seller():
    book = CicProfileBook.load()
    sellers = {seller for seller, _ in demo_source(NOW).records}
    assert sellers <= set(book.sellers)


def test_report_aggregates_facilities(tmp_path):
    worse = [1] * (HISTORY_MONTHS - 3) + [3, 2, 2]
    book = CicProfileBook.load(
        book_file(
            tmp_path,
            {
                "s1": {
                    "recent_inquiries_90d": 4,
                    "facilities": [
                        facility(outstanding=10_000_000),
                        facility(outstanding=25_000_000, group=2, history=worse),
                    ],
                }
            },
        )
    )
    report = SimulatedCicSource(book).inquire("s1", datetime(2026, 2, 10, tzinfo=UTC))
    assert report["bureau_group"] == 2
    assert report["outstanding"] == 35_000_000
    assert report["worst_group_12m"] == 3
    assert report["recent_inquiries_90d"] == 4
    assert report["has_credit_history"] is True
    assert [f["opened_month"] for f in report["facilities"]] == ["2025-11", "2025-11"]
    assert report["facilities"][1]["history_12m"] == worse


def test_profiles_hash_changes_with_content_and_is_recorded(tmp_path):
    first = CicProfileBook.load(book_file(tmp_path, {"s1": {"facilities": [facility()]}}))
    same = CicProfileBook.load(book_file(tmp_path, {"s1": {"facilities": [facility()]}}))
    edited = CicProfileBook.load(
        book_file(tmp_path, {"s1": {"facilities": [facility(outstanding=1)]}})
    )
    assert first.profiles_hash == same.profiles_hash != edited.profiles_hash
    source = SimulatedCicSource(first)
    assert source.inquire("s1", NOW)["profiles_hash"] == first.profiles_hash
    assert source.describe()["profiles_hash"] == first.profiles_hash


def test_unknown_seller_reads_default_profile(tmp_path):
    book = CicProfileBook.load(book_file(tmp_path, {}))
    report = SimulatedCicSource(book).inquire("nobody", NOW)
    assert report["seller_id"] == "nobody"
    assert report["has_credit_history"] is False
    assert (report["bureau_group"], report["outstanding"]) == (NO_HISTORY_GROUP, 0)
    assert report["facilities"] == []


@pytest.mark.parametrize(
    "bad",
    [
        facility(group=6),
        facility(outstanding=-1),
        facility(limit="50tr"),
        facility(history=[1] * (HISTORY_MONTHS - 1)),
        facility(group=2, history=[1] * HISTORY_MONTHS),
    ],
)
def test_invalid_profile_refuses_to_load(tmp_path, bad):
    with pytest.raises(WorkflowError) as error:
        CicProfileBook.load(book_file(tmp_path, {"s1": {"facilities": [bad]}}))
    assert (error.value.code, error.value.status) == ("CIC_PROFILES_INVALID", 503)


def test_missing_profile_file_is_a_config_error(tmp_path):
    with pytest.raises(WorkflowError) as error:
        CicProfileBook.load(tmp_path / "absent.json")
    assert error.value.code == "CIC_PROFILES_INVALID"


@pytest.mark.parametrize(
    ("group", "status", "r06"), [(2, "PENDING_REVIEW", True), (3, "ESCALATED", False)]
)
def test_debt_group_drives_r06(tmp_path, group, status, r06):
    book = CicProfileBook.load(
        book_file(tmp_path, {"demo-b": {"facilities": [facility(group=group, history=None)]}})
    )
    service = service_with(book)
    case = service.create(Plan(**plan()), f"cic-{group}")
    assert case.status == status
    results = {r["rule_id"]: r for r in case.runs[-1]["policy"]["results"]}
    assert results["R06"]["passed"] is r06
    e6a = next(e for e in case.evidence if e["code"] == "E6a")
    assert read_evidence(case, e6a["evidence_id"])["payload"]["facilities"][0]["group"] == group


def test_unknown_cic_source_is_rejected(monkeypatch):
    monkeypatch.setattr(routes.config, "WORKFLOW_MODE", "demo")
    monkeypatch.setattr(routes.config, "WORKFLOW_CIC_SOURCE", "bureau")
    routes.get_service.cache_clear()
    try:
        with pytest.raises(WorkflowError) as error:
            routes.get_service()
    finally:
        routes.get_service.cache_clear()
    assert error.value.code == "INVALID_WORKFLOW_CIC_SOURCE"
