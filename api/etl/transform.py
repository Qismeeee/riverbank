from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import defaultdict

from .models import ProductRow, RawRun, Report, ShopRow, Snapshot, ValidationError
from .segment import Segmenter


class Transformer(ABC):
    @abstractmethod
    def transform(self, run: RawRun, snapshot_date: str) -> Snapshot: ...


class AddlivetagTransformer(Transformer):
    GIFT = (re.compile(r"không bán", re.I), re.compile(r"^\s*\[[^\]]*quà tặng[^\]]*\]", re.I))
    TRACKED_MIN_DAYS = 8
    MERGE_FILL = ("name", "rating", "cat_id", "commission_rate", "revenue_all")

    def __init__(self, segmenter: Segmenter | None = None):
        self.segmenter = segmenter or Segmenter()

    def transform(self, run: RawRun, snapshot_date: str) -> Snapshot:
        report = Report()
        meta = {int(s["shop_id"]): s for s in run.manifest.shops}
        run_id = run.manifest.run_id
        candidates = self._collect(run, meta, snapshot_date, run_id, report)
        products = self._clean(candidates.values(), report)
        shops = self._derive(products, run, meta, snapshot_date, run_id)
        self._validate(shops, products)
        report.shops_out, report.products_out = len(shops), len(products)
        return Snapshot(snapshot_date, shops, products, report)

    def _collect(self, run, meta, snapshot_date, run_id, report) -> dict[int, ProductRow]:
        candidates: dict[int, ProductRow] = {}
        for raw_path, payload in run.market.items():
            for p in payload.get("products", []):
                report.products_in += 1
                row = self._normalize(p, None, snapshot_date, run_id, raw_path)
                if row.shop_id in meta:
                    self._merge(candidates, row, report)
        for shop_id, payload in run.shops.items():
            for p in payload.get("products", []):
                report.products_in += 1
                row = self._normalize(p, shop_id, snapshot_date, run_id, meta[shop_id]["path"])
                self._merge(candidates, row, report)
        return candidates

    def _clean(self, rows, report: Report) -> list[ProductRow]:
        out = []
        for row in rows:
            if row.item_id is None or row.shop_id is None:
                raise ValidationError(f"thiếu khóa: {row}")
            if row.price <= 0:
                report.dropped_price += 1
            elif row.sales_total < 0 or (row.sold_30d or 0) < 0:
                report.dropped_negative += 1
            elif self.is_gift(row.name):
                report.dropped_gift += 1
            else:
                if row.sold_30d is not None and row.sold_30d > row.sales_total:
                    row.sold_30d = row.sales_total
                    report.fixed_sold_over_total += 1
                out.append(row)
        return out

    def _derive(self, products, run, meta, snapshot_date, run_id) -> list[ShopRow]:
        by_shop: dict[int, list[ProductRow]] = defaultdict(list)
        for p in products:
            by_shop[p.shop_id].append(p)
        shops = []
        for shop_id, rows in by_shop.items():
            tracked = [r for r in rows if self._is_tracked(r)]
            revenue_all = [r.revenue_all or r.price * r.sales_total for r in rows]
            gmv_all = sum(revenue_all)
            gmv_30d = sum(r.revenue_30d or 0 for r in tracked)
            sold_30d = sum(r.sold_30d or 0 for r in tracked)
            ratings = [r.rating for r in rows if r.rating]
            name = self._shop_name(shop_id, rows, run, meta)
            revenue_hint = max(gmv_30d, int(meta[shop_id].get("revenue_30d_src") or 0))
            shops.append(
                ShopRow(
                    shop_id=shop_id,
                    snapshot_date=snapshot_date,
                    run_id=run_id,
                    shop_name=name,
                    rating=round(sum(ratings) / len(ratings), 2) if ratings else None,
                    archetype=meta[shop_id]["archetype"],
                    segment=self.segmenter.classify(name, revenue_hint) or "sme",
                    n_products=len(rows),
                    n_tracked=len(tracked),
                    gmv_all=gmv_all,
                    gmv_30d=gmv_30d,
                    sold_30d=sold_30d,
                    aov=gmv_30d // sold_30d if sold_30d else None,
                    top3_share=round(sum(sorted(revenue_all, reverse=True)[:3]) / gmv_all, 3)
                    if gmv_all
                    else None,
                    raw_path=meta[shop_id]["path"],
                )
            )
        return shops

    def _is_tracked(self, r: ProductRow) -> bool:
        return (r.days_tracked or 0) >= self.TRACKED_MIN_DAYS and r.sold_30d is not None

    @staticmethod
    def _shop_name(shop_id, rows, run: RawRun, meta) -> str | None:
        if meta[shop_id].get("shop_name"):
            return meta[shop_id]["shop_name"]
        for p in run.shops.get(shop_id, {}).get("products", []):
            if p.get("shopName"):
                return p["shopName"]
        ids = {r.item_id for r in rows}
        for payload in run.market.values():
            for p in payload.get("products", []):
                if p.get("shopName") and _int(p.get("itemId")) in ids:
                    return p["shopName"]
        return None

    def _merge(self, candidates: dict[int, ProductRow], row: ProductRow, report: Report) -> None:
        if row.item_id is None:
            return
        old = candidates.get(row.item_id)
        if old is None:
            candidates[row.item_id] = row
            return
        report.merged_duplicates += 1
        keep, other = (old, row) if old.days_tracked is not None else (row, old)
        for f in self.MERGE_FILL:
            if getattr(keep, f) in (None, "") and getattr(other, f) not in (None, ""):
                setattr(keep, f, getattr(other, f))
        if keep.sales_total == 0 and other.sales_total:
            keep.sales_total = other.sales_total
        candidates[row.item_id] = keep

    @staticmethod
    def _normalize(p: dict, shop_id, snapshot_date, run_id, raw_path) -> ProductRow:
        return ProductRow(
            item_id=_int(p.get("itemId") or p.get("item_id")),
            shop_id=_int(p.get("shopId") or p.get("shop_id") or shop_id),
            snapshot_date=snapshot_date,
            run_id=run_id,
            name=str(p.get("productName") or p.get("name") or ""),
            price=_int(p.get("price")) or _int(p.get("priceMin"), 0),
            sales_total=_int(p.get("sales"), 0),
            sold_30d=_int(p.get("sold30d")),
            revenue_30d=_int(p.get("revenue30d")),
            revenue_all=_int(p.get("revenueAll")),
            days_tracked=_int(p.get("daysTracked")),
            rating=_float(p.get("rating")),
            cat_id=_int(p.get("catId")) or (_int(p["catIds"][0]) if p.get("catIds") else None),
            commission_rate=_float(p.get("commissionRate")),
            raw_path=raw_path,
        )

    @classmethod
    def is_gift(cls, name: str) -> bool:
        return any(pattern.search(name or "") for pattern in cls.GIFT)

    @staticmethod
    def _validate(shops: list[ShopRow], products: list[ProductRow]) -> None:
        for table, keys in (
            ("product_snapshot", [(p.item_id, p.snapshot_date) for p in products]),
            ("shop_snapshot", [(s.shop_id, s.snapshot_date) for s in shops]),
        ):
            seen, dup = set(), set()
            for k in keys:
                (dup if k in seen else seen).add(k)
            if dup:
                raise ValidationError(f"{table}: trùng khóa {sorted(dup)[:5]}")


def _int(v, default=None):
    if v in (None, ""):
        return default
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _float(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None
