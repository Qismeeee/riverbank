from __future__ import annotations

from pathlib import Path

from .extract import Extractor
from .load import Loader
from .models import Manifest, RawRun, Snapshot
from .store import RawStore
from .transform import Transformer


class Pipeline:
    def __init__(self, store: RawStore, extractor: Extractor, transformer: Transformer):
        self.store = store
        self.extractor = extractor
        self.transformer = transformer

    def extract(self, run_date: str) -> Manifest:
        return self.extractor.extract(self.store, run_date)

    def read_raw(self) -> RawRun:
        manifest = Manifest.from_dict(self.store.read("manifest.json"))
        market = {
            m["path"]: self.store.read(f"market/{Path(m['path']).name}")
            for m in manifest.market
            if m.get("path")
        }
        shops = {
            int(s["shop_id"]): self.store.read(f"shop_products/{s['shop_id']}.json")
            for s in manifest.shops
            if s["status"] == "ok"
        }
        return RawRun(manifest, market, shops)

    def transform(self, run_date: str) -> tuple[RawRun, Snapshot]:
        run = self.read_raw()
        snapshot = self.transformer.transform(run, run_date)
        self.store.write("_report.json", snapshot.report.as_dict())
        return run, snapshot

    def load(self, loader: Loader, run: RawRun, snapshot: Snapshot) -> None:
        loader.load(run.manifest, snapshot)
