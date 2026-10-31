import base64
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from .models import Case, WorkflowError

CasePosition = tuple[datetime, str]


class CaseCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    scope: str
    created_at: AwareDatetime
    case_id: UUID


def case_position(case: Case) -> CasePosition:
    return datetime.fromisoformat(case.created_at).astimezone(UTC), case.case_id


def encode_cursor(case: Case, scope: str) -> str:
    created_at, case_id = case_position(case)
    cursor = CaseCursor(version=1, scope=scope, created_at=created_at, case_id=UUID(case_id))
    return base64.urlsafe_b64encode(cursor.model_dump_json().encode()).rstrip(b"=").decode()


def decode_cursor(token: str, scope: str) -> CasePosition:
    try:
        data = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        cursor = CaseCursor.model_validate_json(data)
        if cursor.scope != scope:
            raise ValueError("Cursor scope mismatch")
    except (ValueError, TypeError):
        raise WorkflowError("INVALID_CURSOR", 422) from None
    return cursor.created_at.astimezone(UTC), str(cursor.case_id)


def past_cursor(case: Case, after: CasePosition | None, descending: bool) -> bool:
    if after is None:
        return True
    return case_position(case) < after if descending else case_position(case) > after
