from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import config

from .addlivetag import AddlivetagClient
from .extract import AddlivetagExtractor
from .load import PostgresLoader
from .pipeline import Pipeline
from .store import LocalRawStore, MirroredRawStore, S3RawStore
from .transform import AddlivetagTransformer

RAW_LOCAL = Path(__file__).resolve().parents[2] / "data" / "raw"


def build_pipeline(run_date: str, raw: str, api_key: str = "") -> Pipeline:
    prefix = f"{AddlivetagExtractor.source}/{run_date}"
    if raw == "s3":
        store = S3RawStore(config.S3_BUCKET, prefix, config.AWS_REGION)
    else:
        store = LocalRawStore(RAW_LOCAL, prefix)
        if raw == "mirror" and config.S3_BUCKET:
            store = MirroredRawStore(store, S3RawStore(config.S3_BUCKET, prefix, config.AWS_REGION))
    extractor = AddlivetagExtractor(AddlivetagClient(api_key)) if api_key else None
    return Pipeline(store, extractor, AddlivetagTransformer())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["extract", "transform", "load", "all"])
    ap.add_argument("--run-date", default=date.today().isoformat())
    ap.add_argument("--raw", choices=["mirror", "local", "s3"], default="mirror")
    a = ap.parse_args(argv)

    need_key = a.step in ("extract", "all")
    pipeline = build_pipeline(a.run_date, a.raw, config.ADDLIVETAG_API_KEY if need_key else "")

    if need_key:
        m = pipeline.extract(a.run_date)
        ok_kw = sum(1 for k in m.market if k.get("path"))
        ok_shop = sum(1 for s in m.shops if s["status"] == "ok")
        print(f"extract: {ok_kw}/{len(m.market)} từ khóa ok, {ok_shop}/{len(m.shops)} shop ok")
        print(f"raw: {pipeline.store.root}")

    if a.step in ("transform", "load", "all"):
        run, snapshot = pipeline.transform(a.run_date)
        print(f"transform: {snapshot.report.as_dict()}")

    if a.step in ("load", "all"):
        url = config.database_url()
        if not url:
            print("load: DATABASE_URL trống, bỏ qua", file=sys.stderr)
            return 1
        import psycopg

        with psycopg.connect(url) as conn:
            loader = PostgresLoader(conn)
            loader.apply_schema()
            pipeline.load(loader, run, snapshot)
        print(f"load: {len(snapshot.shops)} shop, {len(snapshot.products)} sản phẩm vào Postgres")
    return 0


if __name__ == "__main__":
    sys.exit(main())
