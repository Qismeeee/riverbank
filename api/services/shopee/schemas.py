from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Order:
    order_sn: str
    create_time: int
    order_status: str
    total_amount: int
    buyer_user_id: int | None = None
    item_count: int = 1


@dataclass(frozen=True)
class EscrowDetail:
    order_sn: str
    escrow_amount: int
    buyer_total_amount: int
    commission_fee: int = 0
    service_fee: int = 0
    seller_transaction_fee: int = 0
    escrow_release_time: int | None = None


@dataclass(frozen=True)
class ReturnCase:
    return_sn: str
    order_sn: str
    create_time: int
    status: str
    refund_amount: int = 0


@dataclass(frozen=True)
class ShopInfo:
    shop_id: int
    shop_name: str
    region: str
    status: str
    shop_created_time: int | None = None
    rating_star: float | None = None


@dataclass(frozen=True)
class ShopData:
    orders: list[Order]
    escrows: list[EscrowDetail]
    returns: list[ReturnCase]


@dataclass
class WeeklySeries:
    shop_id: int | str
    week_start: list[str]
    gmv: list[int]
    payout: list[int]
    orders: list[int]
    returns: list[int]
    refund_amount: list[int]
    evidence: dict = field(default_factory=dict)

    @property
    def return_rate(self) -> float:
        total_orders, total_returns = sum(self.orders), sum(self.returns)
        return total_returns / total_orders if total_orders else 0.0
