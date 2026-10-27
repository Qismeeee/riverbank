from functools import lru_cache

import psycopg
from psycopg_pool import ConnectionPool

import config


class RotatingCredentialsPool(ConnectionPool):
    def _connect(self, timeout: float | None = None):
        try:
            return super()._connect(timeout)
        except psycopg.OperationalError:
            fresh = config.database_url(refresh=True)
            if not fresh or fresh == self.conninfo:
                raise
            self.conninfo = fresh
            return super()._connect(timeout)


@lru_cache(maxsize=None)
def connection_pool(url: str) -> ConnectionPool:
    return RotatingCredentialsPool(
        url,
        min_size=1,
        max_size=config.DB_POOL_MAX_SIZE,
        timeout=config.DB_POOL_TIMEOUT_SECONDS,
        check=ConnectionPool.check_connection,
        open=True,
    )


def connect(url: str):
    return connection_pool(url).connection()
