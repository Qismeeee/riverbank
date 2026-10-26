from functools import lru_cache

from psycopg_pool import ConnectionPool

import config


@lru_cache(maxsize=None)
def connection_pool(url: str) -> ConnectionPool:
    return ConnectionPool(
        url,
        min_size=1,
        max_size=config.DB_POOL_MAX_SIZE,
        timeout=config.DB_POOL_TIMEOUT_SECONDS,
        check=ConnectionPool.check_connection,
        open=True,
    )


def connect(url: str):
    return connection_pool(url).connection()
