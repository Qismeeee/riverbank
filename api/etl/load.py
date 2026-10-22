from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, fields
from pathlib import Path

from .models import Manifest, ProductRow, ShopRow, Snapshot

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


class Loader(ABC):
    @abstractmethod
    def load(self, manifest: Manifest, snapshot: Snapshot) -> None: ...


class PostgresLoader(Loader):
    SHOP_COLS = [f.name for f in fields(ShopRow)]
    PRODUCT_COLS = [f.name for f in fields(ProductRow)]

    def __init__(self, conn, schema_path: Path = SCHEMA_PATH):
        self.conn = conn
        self.schema_path = schema_path

    def apply_schema(self) -> None:
        with self.conn.transaction():
            self.conn.execute(self.schema_path.read_text(encoding="utf-8"))

    def load(self, manifest: Manifest, snapshot: Snapshot) -> None:
        with self.conn.transaction():
            self._replace_run(manifest, snapshot)
            with self.conn.cursor() as cur:
                cur.executemany(
                    self.upsert_sql("shop_snapshot", self.SHOP_COLS, ("shop_id", "snapshot_date")),
                    [tuple(asdict(s)[c] for c in self.SHOP_COLS) for s in snapshot.shops],
                )
                cur.executemany(
                    self.upsert_sql(
                        "product_snapshot", self.PRODUCT_COLS, ("item_id", "snapshot_date")
                    ),
                    [tuple(asdict(p)[c] for c in self.PRODUCT_COLS) for p in snapshot.products],
                )

    def _replace_run(self, manifest: Manifest, snapshot: Snapshot) -> None:
        self.conn.execute("delete from product_snapshot where run_id = %s", (manifest.run_id,))
        self.conn.execute("delete from shop_snapshot where run_id = %s", (manifest.run_id,))
        self.conn.execute(
            "insert into etl_run "
            "(run_id, source, pulled_at, raw_prefix, n_shops, n_products, note) "
            "values (%s, %s, %s, %s, %s, %s, %s) on conflict (run_id) do update set "
            "pulled_at = excluded.pulled_at, n_shops = excluded.n_shops, "
            "n_products = excluded.n_products, note = excluded.note",
            (
                manifest.run_id,
                manifest.source,
                manifest.pulled_at,
                manifest.raw_prefix,
                len(snapshot.shops),
                len(snapshot.products),
                str(snapshot.report.as_dict()),
            ),
        )

    @staticmethod
    def upsert_sql(table: str, cols: list[str], key: tuple[str, ...]) -> str:
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in key)
        return (
            f"insert into {table} ({', '.join(cols)}) values ({', '.join('%s' for _ in cols)}) "
            f"on conflict ({', '.join(key)}) do update set {updates}"
        )
