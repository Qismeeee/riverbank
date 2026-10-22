from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from .schemas import EscrowDetail, Order, ReturnCase, ShopData, WeeklySeries

PAID_STATUSES = {"READY_TO_SHIP", "PROCESSED", "SHIPPED", "TO_CONFIRM_RECEIVE", "COMPLETED"}
RETURN_COUNTED = {"REQUESTED", "ACCEPTED", "JUDGING", "REFUND_PAID", "CLOSED"}


def week_monday(ts: int) -> date:
    d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
    return d - timedelta(days=d.weekday())


class WeeklyAggregator:
    def __init__(self, weeks: int = 12, as_of: date | None = None):
        self.weeks = weeks
        self.as_of = as_of or date.today()

    def build(self, shop_id: int | str, data: ShopData) -> WeeklySeries:
        starts = self._week_starts()
        gmv, count = self._orders(data.orders)
        payout = self._payout(data.escrows)
        returns, refund = self._returns(data.returns)
        pick = lambda m: [m.get(d, 0) for d in starts]  # noqa: E731
        return WeeklySeries(
            shop_id=shop_id,
            week_start=[d.isoformat() for d in starts],
            gmv=pick(gmv),
            payout=pick(payout),
            orders=pick(count),
            returns=pick(returns),
            refund_amount=pick(refund),
            evidence={
                "orders": len(data.orders),
                "escrows": len(data.escrows),
                "returns": len(data.returns),
            },
        )

    def _week_starts(self) -> list[date]:
        last = self.as_of - timedelta(days=self.as_of.weekday())
        return [last - timedelta(weeks=self.weeks - 1 - i) for i in range(self.weeks)]

    @staticmethod
    def _orders(orders: list[Order]) -> tuple[dict, dict]:
        gmv, count = defaultdict(int), defaultdict(int)
        for o in orders:
            if o.order_status in PAID_STATUSES:
                w = week_monday(o.create_time)
                gmv[w] += o.total_amount
                count[w] += 1
        return gmv, count

    @staticmethod
    def _payout(escrows: list[EscrowDetail]) -> dict:
        payout = defaultdict(int)
        for e in escrows:
            if e.escrow_release_time:
                payout[week_monday(e.escrow_release_time)] += e.escrow_amount
        return payout

    @staticmethod
    def _returns(returns: list[ReturnCase]) -> tuple[dict, dict]:
        count, refund = defaultdict(int), defaultdict(int)
        for r in returns:
            if r.status in RETURN_COUNTED:
                w = week_monday(r.create_time)
                count[w] += 1
                refund[w] += r.refund_amount
        return count, refund
