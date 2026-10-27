import os
import sys
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))


def validate_connection(url: str) -> None:
    if not url:
        raise ValueError("WORKFLOW_TEST_DATABASE_URL is required")
    try:
        params = conninfo_to_dict(url)
    except Exception:
        raise ValueError("invalid local test database configuration") from None
    if (
        params.get("host") not in {"127.0.0.1", "localhost"}
        or params.get("dbname") != "riverbank_test"
    ):
        raise ValueError("integration database must be local and named riverbank_test")


def main() -> int:
    os.environ["RIVERBANK_ENV"] = "local"
    import pytest

    import config

    try:
        validate_connection(config.WORKFLOW_TEST_DATABASE_URL)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    os.environ["DATABASE_URL"] = config.WORKFLOW_TEST_DATABASE_URL
    return pytest.main(["-q", "tests/test_underwriting_postgres.py", "tests/test_etl.py"])


if __name__ == "__main__":
    raise SystemExit(main())
