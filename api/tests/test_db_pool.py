import psycopg
import pytest
from psycopg_pool import ConnectionPool

import config
from db.pool import RotatingCredentialsPool

OLD = "postgresql://riverbank:old@db:5432/riverbank"
NEW = "postgresql://riverbank:new@db:5432/riverbank"


@pytest.fixture
def attempts(monkeypatch):
    tried = []

    def connect(pool, timeout=None):
        tried.append(pool.conninfo)
        if pool.conninfo != NEW:
            raise psycopg.OperationalError("password authentication failed")
        return "conn"

    monkeypatch.setattr(ConnectionPool, "_connect", connect)
    return tried


def test_rotated_password_is_reread_and_kept(monkeypatch, attempts):
    monkeypatch.setattr(config, "database_url", lambda refresh=False: NEW if refresh else OLD)
    pool = RotatingCredentialsPool(OLD, open=False)
    assert pool._connect() == "conn"
    assert pool._connect() == "conn"
    assert attempts == [OLD, NEW, NEW]


@pytest.mark.parametrize("fresh", [OLD, ""])
def test_failure_without_new_credentials_is_raised(monkeypatch, attempts, fresh):
    monkeypatch.setattr(config, "database_url", lambda refresh=False: fresh)
    with pytest.raises(psycopg.OperationalError):
        RotatingCredentialsPool(OLD, open=False)._connect()
    assert attempts == [OLD]


def test_database_url_quotes_secret_and_refreshes(monkeypatch):
    secrets = iter(
        [
            {"username": "riverbank", "password": "a#b?c:d"},
            {"username": "riverbank", "password": "z"},
        ]
    )
    calls = []

    def secret(secret_id):
        calls.append(secret_id)
        return next(secrets)

    secret.cache_clear = lambda: calls.append("clear")
    monkeypatch.setattr(config, "_secret", secret)
    monkeypatch.setattr(config, "ENV", "prod")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_SECRET_ID", "rds!db-x")
    monkeypatch.setenv("DB_HOST", "db")
    assert config.database_url() == "postgresql://riverbank:a%23b%3Fc%3Ad@db:5432/riverbank"
    assert config.database_url(refresh=True) == "postgresql://riverbank:z@db:5432/riverbank"
    assert calls == ["rds!db-x", "clear", "rds!db-x"]
