import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import config

from .models import WorkflowError

bearer = HTTPBearer(
    auto_error=False,
    scheme_name="WorkflowDemoBearer",
    description="Token demo được cấp riêng. Token xác định seller/role; không nhập tiền tố Bearer.",
)


@dataclass(frozen=True)
class Actor:
    subject: str
    role: str


def require_demo_configuration() -> None:
    if config.WORKFLOW_MODE != "demo":
        raise WorkflowError("WORKFLOW_DEMO_DISABLED", 503)
    if config.ENV != "local":
        if config.ENV != "prod" or config.WORKFLOW_AUTH != "bearer":
            raise WorkflowError("VERIFIED_IDENTITY_ADAPTER_REQUIRED", 503)
        if config.WORKFLOW_STORAGE != "memory" or config.WORKFLOW_FEATURES != "demo":
            raise WorkflowError("PRODUCTION_DEMO_REQUIRES_ISOLATED_DATA", 503)


def invalid_token() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "INVALID_OR_EXPIRED_DEMO_TOKEN", "details": []},
        headers={"WWW-Authenticate": "Bearer"},
    )


def validate_credentials(document: dict) -> list[tuple[str, Actor, datetime]]:
    records = document["credentials"]
    if document.get("schema_version") != 1 or not isinstance(records, list):
        raise ValueError("invalid credentials schema")
    if not 1 <= len(records) <= 100:
        raise ValueError("invalid credentials count")
    verified = []
    seen = set()
    for record in records:
        if set(record) != {"token_sha256", "subject", "role", "expires_at"}:
            raise ValueError("unexpected credential fields")
        token_hash = record["token_sha256"]
        subject, role = record["subject"], record["role"]
        expires = datetime.fromisoformat(record["expires_at"])
        if (
            not isinstance(token_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", token_hash) is None
            or token_hash in seen
            or not isinstance(subject, str)
            or not subject.strip()
            or len(subject) > 100
            or role not in {"seller", "underwriter"}
            or (role == "seller" and subject not in {"demo-a", "demo-b", "demo-c"})
            or expires.tzinfo is None
        ):
            raise ValueError("invalid credential")
        seen.add(token_hash)
        verified.append((token_hash, Actor(subject, role), expires))
    return verified


def authenticate_token(token: str, now: datetime) -> Actor:
    if not 32 <= len(token) <= 256:
        raise invalid_token()
    try:
        verified = validate_credentials(config.workflow_demo_credentials())
    except Exception:
        raise WorkflowError("DEMO_AUTH_UNAVAILABLE", 503) from None
    supplied = hashlib.sha256(token.encode("utf-8")).hexdigest()
    for token_hash, actor, expires in verified:
        if hmac.compare_digest(supplied, token_hash) and now < expires:
            return actor
    raise invalid_token()


def get_actor(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    actor_id: Annotated[str | None, Header(alias="X-Actor-ID")] = None,
    actor_role: Annotated[str | None, Header(alias="X-Actor-Role")] = None,
) -> Actor:
    require_demo_configuration()
    if config.WORKFLOW_AUTH == "bearer":
        if credentials is None:
            raise invalid_token()
        return authenticate_token(credentials.credentials, datetime.now(UTC))
    if config.WORKFLOW_AUTH != "local_headers" or config.ENV != "local":
        raise WorkflowError("VERIFIED_IDENTITY_ADAPTER_REQUIRED", 503)
    if not actor_id or not actor_id.strip():
        raise WorkflowError("AUTHENTICATION_REQUIRED", 401)
    if actor_role not in {"seller", "underwriter"}:
        raise WorkflowError("ROLE_NOT_ALLOWED", 403)
    return Actor(actor_id, actor_role)
