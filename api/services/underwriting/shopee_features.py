from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from services.shopee.source import ShopeeSource
from services.shopee.weekly import WeeklyAggregator

from .models import WorkflowError
from .sources import FeatureSource, validate_consent


@dataclass(frozen=True)
class ShopeeRegistration:
    source: ShopeeSource
    source_kind: str
    consent: dict
    platform_supported_amount: int
    raw_refs: tuple[str, ...]
    shop_age_months: int | None = None


class ShopeeFeatureSource(FeatureSource):
    def __init__(self, registrations: dict[tuple[str, str], ShopeeRegistration]):
        self.registrations = registrations

    def fetch(self, seller_id: str, shop_id: str, now: datetime) -> tuple[dict, dict]:
        record = self.registrations.get((seller_id, shop_id))
        if record is None:
            raise WorkflowError("SELLER_SHOP_SCOPE_NOT_FOUND", 403)
        validate_consent(record.consent, now)
        if record.source_kind not in {"seller_export", "openapi"}:
            raise WorkflowError("UNSUPPORTED_SHOPEE_SOURCE", 422)
        monday = now.date() - timedelta(days=now.weekday())
        start = datetime.combine(monday - timedelta(weeks=12), time(), tzinfo=UTC)
        end = datetime.combine(monday, time(), tzinfo=UTC)
        data = record.source.fetch(int(start.timestamp()), int(end.timestamp()))
        # Missing escrow releases must never become fabricated zero-payout history.
        if not any(
            e.escrow_release_time
            and int(start.timestamp()) <= e.escrow_release_time < int(end.timestamp())
            for e in data.escrows
        ):
            raise WorkflowError("PAYOUT_HISTORY_REQUIRED", 422)
        weekly = WeeklyAggregator(weeks=12, as_of=monday - timedelta(days=7)).build(shop_id, data)
        return record.consent, {
            "seller_id": seller_id,
            "shop_id": shop_id,
            "source": record.source_kind,
            "payout_origin": "observed",
            "disclosure": f"payout từ {record.source_kind}",
            "platform_supported_amount": record.platform_supported_amount,
            "shop_age_months": record.shop_age_months,
            "return_rate": weekly.return_rate if sum(weekly.orders) else None,
            "weekly_payouts": [
                {"week_start": week, "amount": amount}
                for week, amount in zip(weekly.week_start, weekly.payout, strict=True)
            ],
            "raw_refs": list(record.raw_refs),
            "etl_run_id": None,
        }
