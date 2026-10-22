from __future__ import annotations

import json
import re
import unicodedata
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from .addlivetag import AddlivetagClient
from .models import Manifest
from .segment import Segmenter
from .store import RawStore

DEFAULT_KEYWORDS = json.loads((Path(__file__).parent / "keywords.json").read_text(encoding="utf-8"))


def slug(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


class Extractor(ABC):
    source: str

    @abstractmethod
    def extract(self, store: RawStore, run_date: str) -> Manifest: ...


class AddlivetagExtractor(Extractor):
    source = "addlivetag"

    def __init__(
        self,
        client: AddlivetagClient,
        keywords: dict[str, list[str]] | None = None,
        segmenter: Segmenter | None = None,
        sme_per_keyword: int = 7,
        brand_per_keyword: int = 1,
        max_shops: int = 150,
    ):
        self.client = client
        self.keywords = keywords or DEFAULT_KEYWORDS
        self.segmenter = segmenter or Segmenter()
        self.sme_per_keyword = sme_per_keyword
        self.brand_per_keyword = brand_per_keyword
        self.max_shops = max_shops

    def extract(self, store: RawStore, run_date: str) -> Manifest:
        manifest = Manifest(
            run_id=f"{self.source}_{run_date}",
            source=self.source,
            run_date=run_date,
            pulled_at=datetime.now(timezone.utc).isoformat(),
            raw_prefix=store.root,
        )
        candidates: dict[int, dict] = {}
        for archetype, keywords in self.keywords.items():
            for keyword in keywords:
                self._market(store, manifest, candidates, archetype, keyword)
        for meta in list(candidates.values())[: self.max_shops]:
            manifest.shops.append(self._shop(store, manifest, meta))
        store.write("manifest.json", manifest.to_dict())
        return manifest

    def _market(self, store, manifest, candidates, archetype, keyword) -> None:
        entry = {"archetype": archetype, "keyword": keyword, "path": None, "status": "ok"}
        try:
            data = self.client.market_search(keyword)
        except Exception as e:  # noqa: BLE001
            entry["status"] = str(e)[:200]
            manifest.market.append(entry)
            return
        entry["path"] = store.write(f"market/{archetype}__{slug(keyword)}.json", data)
        manifest.market.append(entry)
        for meta in self._pick(data, archetype, keyword):
            candidates.setdefault(meta["shop_id"], meta)

    def _pick(self, data: dict, archetype: str, keyword: str) -> list[dict]:
        names = {}
        for p in data.get("products", []):
            if p.get("shopId") and p.get("shopName"):
                names.setdefault(int(p["shopId"]), p["shopName"])
        quota = {"sme": self.sme_per_keyword, "brand": self.brand_per_keyword}
        picked = []
        for shop in data.get("topShops", []):
            shop_id = int(shop["shopId"])
            revenue = int(shop.get("revenue") or 0)
            segment = self.segmenter.classify(names.get(shop_id), revenue)
            if segment is None or quota[segment] == 0:
                continue
            quota[segment] -= 1
            picked.append(
                {
                    "shop_id": shop_id,
                    "archetype": archetype,
                    "keyword": keyword,
                    "segment": segment,
                    "shop_name": names.get(shop_id),
                    "revenue_30d_src": revenue,
                }
            )
        return picked

    def _shop(self, store, manifest, meta: dict) -> dict:
        shop_id = meta["shop_id"]
        try:
            products, status = self.client.shop_products(shop_id), "ok"
        except Exception as e:  # noqa: BLE001
            products, status = [], f"error: {type(e).__name__}: {e}"[:200]
        path = store.write(
            f"shop_products/{shop_id}.json",
            {
                "shop_id": shop_id,
                "pulled_at": manifest.pulled_at,
                "status": status,
                "products": products,
            },
        )
        name = meta.get("shop_name") or next(
            (p.get("shopName") for p in products if p.get("shopName")), None
        )
        segment = self.segmenter.classify(name, meta["revenue_30d_src"]) or meta["segment"]
        return {
            **meta,
            "shop_name": name,
            "segment": segment,
            "path": path,
            "status": status,
            "n_products": len(products),
        }
