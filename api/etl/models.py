from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Manifest:
    run_id: str
    source: str
    run_date: str
    pulled_at: str
    raw_prefix: str
    market: list[dict] = field(default_factory=list)
    shops: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Manifest:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class RawRun:
    manifest: Manifest
    market: dict[str, dict]
    shops: dict[int, dict]


@dataclass
class ProductRow:
    item_id: int
    shop_id: int
    snapshot_date: str
    run_id: str
    name: str
    price: int
    sales_total: int
    sold_30d: int | None
    revenue_30d: int | None
    revenue_all: int | None
    days_tracked: int | None
    rating: float | None
    cat_id: int | None
    commission_rate: float | None
    raw_path: str


@dataclass
class ShopRow:
    shop_id: int
    snapshot_date: str
    run_id: str
    shop_name: str | None
    rating: float | None
    archetype: str
    segment: str
    n_products: int
    n_tracked: int
    gmv_all: int
    gmv_30d: int
    sold_30d: int
    aov: int | None
    top3_share: float | None
    raw_path: str


@dataclass
class Report:
    products_in: int = 0
    dropped_price: int = 0
    dropped_gift: int = 0
    dropped_negative: int = 0
    merged_duplicates: int = 0
    fixed_sold_over_total: int = 0
    shops_out: int = 0
    products_out: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Snapshot:
    snapshot_date: str
    shops: list[ShopRow]
    products: list[ProductRow]
    report: Report


class ValidationError(ValueError):
    pass
