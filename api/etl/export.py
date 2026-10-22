from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date
from pathlib import Path

import config

TABLES = ("v_shop_features", "shop_snapshot", "product_snapshot", "etl_run")
REPO_EXPORTS = Path(__file__).resolve().parents[2] / "data" / "exports"


def default_out_dir() -> Path:
    try:
        REPO_EXPORTS.mkdir(parents=True, exist_ok=True)
        return REPO_EXPORTS
    except OSError:
        return Path(tempfile.gettempdir()) / "riverbank-exports"


class CsvExporter:
    def __init__(self, conn, out_dir: Path, bucket: str = "", region: str = "ap-southeast-1"):
        self.conn, self.out_dir, self.bucket, self.region = conn, out_dir, bucket, region

    def export(self, run_date: str, tables: tuple[str, ...] = TABLES) -> list[str]:
        folder = self.out_dir / run_date
        folder.mkdir(parents=True, exist_ok=True)
        written = []
        for table in tables:
            path = folder / f"{table}.csv"
            with self.conn.cursor() as cur, open(path, "wb") as f:
                with cur.copy(f"copy (select * from {table}) to stdout with csv header") as copy:
                    for chunk in copy:
                        f.write(chunk)
            written.append(self._publish(path, run_date))
        return written

    def _publish(self, path: Path, run_date: str) -> str:
        if not self.bucket:
            return str(path)
        import boto3

        key = f"exports/{run_date}/{path.name}"
        boto3.client("s3", region_name=self.region).upload_file(str(path), self.bucket, key)
        return f"s3://{self.bucket}/{key}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-date", default=date.today().isoformat())
    ap.add_argument("--out", default="")
    ap.add_argument("--no-s3", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out) if a.out else default_out_dir()
    url = config.database_url()
    if not url:
        print("export: DATABASE_URL trống", file=sys.stderr)
        return 1
    import psycopg

    with psycopg.connect(url) as conn:
        bucket = "" if a.no_s3 else config.S3_BUCKET
        for target in CsvExporter(conn, out, bucket, config.AWS_REGION).export(a.run_date):
            print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
