import base64
import hashlib
import hmac
import json
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field

import config
from services.underwriting.models import WorkflowError
from services.underwriting.service import utc_now

ROLES = ("seller", "underwriter")
MIN_SIGNING_KEY_BYTES = 32


@dataclass(frozen=True)
class Actor:
    subject: str
    role: str


@dataclass(frozen=True)
class Credentials:
    authorization: str | None
    actor_id: str | None
    actor_role: str | None


def _actor(subject: str | None, role: str | None) -> Actor:
    if not subject or not subject.strip():
        raise WorkflowError("AUTHENTICATION_REQUIRED", 401)
    if role not in ROLES:
        raise WorkflowError("ROLE_NOT_ALLOWED", 403)
    return Actor(subject, role)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class TokenSigner:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())

    def __init__(self, key: str, ttl_seconds: int):
        if len(key.encode()) < MIN_SIGNING_KEY_BYTES or ttl_seconds <= 0:
            raise WorkflowError("AUTH_NOT_CONFIGURED", 503)
        self.key, self.ttl = key.encode(), timedelta(seconds=ttl_seconds)

    def _sign(self, message: str) -> str:
        return _b64(hmac.new(self.key, message.encode(), hashlib.sha256).digest())

    def issue(self, actor: Actor, now: datetime) -> tuple[str, datetime]:
        expires = now + self.ttl
        claims = {
            "sub": actor.subject,
            "role": actor.role,
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        }
        body = f"{self.header}.{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
        return f"{body}.{self._sign(body)}", expires

    def verify(self, token: str, now: datetime) -> Actor:
        try:
            header, payload, signature = token.split(".")
            if header != self.header or not hmac.compare_digest(
                signature, self._sign(f"{header}.{payload}")
            ):
                raise ValueError
            claims = json.loads(_unb64(payload))
            expires = int(claims["exp"])
        except (ValueError, KeyError, TypeError):
            raise WorkflowError("INVALID_TOKEN", 401) from None
        if expires <= now.timestamp():
            raise WorkflowError("TOKEN_EXPIRED", 401)
        return _actor(claims.get("sub"), claims.get("role"))


def signer() -> TokenSigner:
    return TokenSigner(config.AUTH_SIGNING_KEY, config.AUTH_TOKEN_TTL_SECONDS)


class IdentityProvider(ABC):
    @abstractmethod
    def authenticate(self, credentials: Credentials) -> Actor: ...


class HeaderIdentity(IdentityProvider):
    def authenticate(self, credentials: Credentials) -> Actor:
        if config.ENV != "local":
            raise WorkflowError("VERIFIED_IDENTITY_ADAPTER_REQUIRED", 503)
        return _actor(credentials.actor_id, credentials.actor_role)


class TokenIdentity(IdentityProvider):
    def authenticate(self, credentials: Credentials) -> Actor:
        scheme, _, token = (credentials.authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise WorkflowError("AUTHENTICATION_REQUIRED", 401)
        return signer().verify(token.strip(), utc_now())


IDENTITY_PROVIDERS: dict[str, type[IdentityProvider]] = {
    "header": HeaderIdentity,
    "token": TokenIdentity,
}


def get_actor(
    authorization: Annotated[str | None, Header()] = None,
    actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    actor_role: Annotated[str | None, Header(alias="X-Actor-Role")] = None,
) -> Actor:
    if config.WORKFLOW_MODE != "demo":
        raise WorkflowError("WORKFLOW_DEMO_DISABLED", 503)
    provider = IDENTITY_PROVIDERS.get(config.WORKFLOW_IDENTITY)
    if provider is None:
        raise WorkflowError("INVALID_WORKFLOW_IDENTITY", 503)
    return provider().authenticate(Credentials(authorization, actor_id, actor_role))


def underwriter(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
    if actor.role != "underwriter":
        raise WorkflowError("UNDERWRITER_REQUIRED", 403)
    return actor


def seller(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
    if actor.role != "seller":
        raise WorkflowError("SELLER_REQUIRED", 403)
    return actor


def seller_of(actor: Actor, seller_id: str) -> None:
    if actor.role != "seller" or actor.subject != seller_id:
        raise WorkflowError("SELLER_SCOPE_MISMATCH", 403)


BankUser = Annotated[Actor, Depends(underwriter)]
SellerUser = Annotated[Actor, Depends(seller)]
Principal = Annotated[Actor, Depends(get_actor)]


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    role: Literal["seller", "underwriter"]
    passcode: str = Field(min_length=1, max_length=256)


class ActorResponse(BaseModel):
    subject: str
    role: str


class TokenResponse(ActorResponse):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class LoginThrottle:
    def __init__(self, max_failures: int, window_seconds: int):
        self.max_failures, self.window = max_failures, timedelta(seconds=window_seconds)
        self._failures: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = Lock()

    def _recent(self, key: str, now: datetime) -> deque[datetime]:
        failures = self._failures[key]
        while failures and failures[0] <= now - self.window:
            failures.popleft()
        if not failures:
            del self._failures[key]
            return deque()
        return failures

    def check(self, key: str, now: datetime) -> None:
        with self._lock:
            if len(self._recent(key, now)) >= self.max_failures:
                raise WorkflowError("TOO_MANY_ATTEMPTS", 429)

    def fail(self, key: str, now: datetime) -> None:
        with self._lock:
            self._failures[key].append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


throttle = LoginThrottle(config.AUTH_MAX_FAILURES, config.AUTH_FAILURE_WINDOW_SECONDS)


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
def issue_token(request: TokenRequest, http: Request) -> dict:
    if config.WORKFLOW_MODE != "demo" or config.WORKFLOW_IDENTITY != "token":
        raise WorkflowError("TOKEN_LOGIN_DISABLED", 503)
    if not config.DEMO_PASSCODE:
        raise WorkflowError("AUTH_NOT_CONFIGURED", 503)
    key, now = client_key(http), utc_now()
    throttle.check(key, now)
    if not hmac.compare_digest(request.passcode.encode(), config.DEMO_PASSCODE.encode()):
        throttle.fail(key, now)
        raise WorkflowError("INVALID_CREDENTIALS", 401)
    throttle.reset(key)
    actor = Actor(request.subject, request.role)
    token, expires = signer().issue(actor, utc_now())
    return {
        "subject": actor.subject,
        "role": actor.role,
        "access_token": token,
        "expires_at": expires,
    }


@router.get("/me", response_model=ActorResponse)
def me(actor: Principal) -> dict:
    return {"subject": actor.subject, "role": actor.role}
