import pytest

from routers.limits import limiter


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    limiter.cache_clear()
    yield
    limiter.cache_clear()
