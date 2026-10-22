from __future__ import annotations

import re


class Segmenter:
    BRAND = re.compile(
        r"offic|mall|chính hãng|việt nam|vietnam|flagship|store online|authorized|partner", re.I
    )

    def __init__(self, sme_min: int = 50_000_000, sme_max: int = 3_000_000_000):
        self.sme_min, self.sme_max = sme_min, sme_max

    def classify(self, name: str | None, revenue_30d: int) -> str | None:
        if self.BRAND.search(name or "") or revenue_30d > self.sme_max:
            return "brand"
        if revenue_30d >= self.sme_min:
            return "sme"
        return None
