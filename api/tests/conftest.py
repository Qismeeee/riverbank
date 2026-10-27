import pytest

from routers.limits import limiter
from services.modelgate import model_gate


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    limiter.cache_clear()
    model_gate.cache_clear()
    yield
    limiter.cache_clear()
    model_gate.cache_clear()
