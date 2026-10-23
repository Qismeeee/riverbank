from pathlib import Path

import psycopg

import config
from services.underwriting.evidence import digest


def migrate(conn) -> None:
    with conn.transaction():
        conn.execute("select pg_advisory_xact_lock(70481001)")
        conn.execute(
            "create table if not exists underwriting_migrations "
            "(version text primary key, checksum text not null, applied_at timestamptz "
            "not null default current_timestamp)"
        )
        for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            checksum = digest(sql)
            row = conn.execute(
                "select checksum from underwriting_migrations where version=%s",
                (path.stem,),
            ).fetchone()
            if row:
                if row[0] != checksum:
                    raise RuntimeError(f"migration checksum changed: {path.stem}")
                continue
            conn.execute(sql)
            conn.execute(
                "insert into underwriting_migrations (version,checksum) values (%s,%s)",
                (path.stem, checksum),
            )


def apply(url: str) -> None:
    with psycopg.connect(url) as conn:
        migrate(conn)


def main() -> None:
    url = config.database_url()
    if not url:
        raise SystemExit("DATABASE_URL is required")
    apply(url)
    print("Underwriting migrations applied")


if __name__ == "__main__":
    main()
