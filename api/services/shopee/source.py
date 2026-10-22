from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from .openapi_client import ShopeeClient, ShopToken
from .schemas import ShopData
from .seller_export import SellerExportParser


class ShopeeSource(ABC):
    @abstractmethod
    def fetch(self, time_from: int, time_to: int) -> ShopData: ...


class SellerExportSource(ShopeeSource):
    def __init__(
        self,
        orders_path: str | Path,
        income_path: str | Path,
        returns_path: str | Path | None = None,
        parser: SellerExportParser | None = None,
    ):
        self.paths = (orders_path, income_path, returns_path)
        self.parser = parser or SellerExportParser()

    def fetch(self, time_from: int, time_to: int) -> ShopData:
        orders_path, income_path, returns_path = self.paths
        orders = [
            o for o in self.parser.orders(orders_path) if time_from <= o.create_time < time_to
        ]
        escrows = self.parser.income(income_path)
        returns = self.parser.returns(returns_path) if returns_path else []
        return ShopData(orders, escrows, returns)


class OpenApiSource(ShopeeSource):
    def __init__(self, client: ShopeeClient, token: ShopToken):
        self.client = client
        self.token = token

    def fetch(self, time_from: int, time_to: int) -> ShopData:
        orders = self.client.get_orders(self.token, time_from, time_to)
        escrows = [self.client.get_escrow(self.token, o.order_sn) for o in orders]
        returns = self.client.get_returns(self.token, time_from, time_to)
        return ShopData(orders, escrows, returns)
