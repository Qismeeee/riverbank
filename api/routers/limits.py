from functools import lru_cache
from typing import Annotated

from fastapi import Depends

import config
from routers.auth import Actor, get_actor
from services.ratelimit import RateLimit, RateLimiter
from services.underwriting.service import utc_now


@lru_cache(maxsize=None)
def limiter(scope: str) -> RateLimiter:
    return RateLimiter(RateLimit.parse(config.rate_limit(scope)))


def rate_limited(scope: str):
    def check(actor: Annotated[Actor, Depends(get_actor)]) -> None:
        limiter(scope).hit(f"{actor.role}:{actor.subject}", utc_now())

    return Depends(check)
