from __future__ import annotations

import time

import httpx

BASE = "https://data.addlivetag.com"
RATE_PER_MIN = 150
_MIN_GAP = 60.0 / RATE_PER_MIN


class AddlivetagClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None, sleep=time.sleep):
        if not api_key:
            raise RuntimeError("ADDLIVETAG_API_KEY chưa cấu hình (.env hoặc Secrets Manager)")
        self.http = http or httpx.Client(base_url=BASE, timeout=30)
        self.http.headers["X-API-Key"] = api_key
        self.sleep = sleep
        self._last = 0.0

    def _get(self, path: str, params: dict) -> dict:
        last_err: Exception | None = None
        for attempt in range(5):
            gap = _MIN_GAP - (time.monotonic() - self._last)
            if gap > 0:
                self.sleep(gap)
            self._last = time.monotonic()
            try:
                r = self.http.get(path, params=params)
            except httpx.TransportError as e:
                last_err = e
                self.sleep(2**attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last_err = RuntimeError(f"HTTP {r.status_code}")
                self.sleep(2**attempt)
                continue
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "error":
                raise RuntimeError(f"addlivetag {path}: {data.get('message')}")
            return data
        raise RuntimeError(f"addlivetag {path}: quá 5 lần thử ({last_err})")

    def market_search(self, q: str, limit: int = 200, sort: str = "revenue_30d") -> dict:
        return self._get("/search/market.php", {"q": q, "limit": limit, "sort": sort})

    def shop_products(self, shop_id: int, max_pages: int = 4) -> list[dict]:
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            d = self._get(
                "/offers/shop-products.php", {"shopId": shop_id, "page": page, "limit": 50}
            )
            out += d.get("products", [])
            if not d.get("hasNextPage"):
                break
        return out

    def shop_offer(self, page: int = 1, limit: int = 50) -> list[dict]:
        return self._get("/offers/shop-offer.php", {"page": page, "limit": limit}).get("shops", [])
