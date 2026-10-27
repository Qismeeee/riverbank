import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import config
from services.underwriting.models import WorkflowError

ROLES = ("seller", "underwriter")
SUBJECT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class UserRecord:
    subject: str
    role: str
    full_name: str
    display_name: str

    @classmethod
    def parse(cls, raw: dict) -> "UserRecord":
        user = cls(
            raw["subject"], raw["role"], raw["full_name"].strip(), raw["display_name"].strip()
        )
        if not SUBJECT.match(user.subject) or user.role not in ROLES or not user.full_name:
            raise ValueError(user.subject)
        return user


class UserDirectory:
    def __init__(self, users: list[UserRecord]):
        self.users = {u.subject: u for u in users}
        if len(self.users) != len(users):
            raise ValueError("duplicate subject")

    @classmethod
    def load(cls, path: Path) -> "UserDirectory":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls([UserRecord.parse(item) for item in raw["users"]])
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise WorkflowError("USER_DIRECTORY_INVALID", 503, [type(exc).__name__]) from exc

    def find(self, subject: str, role: str) -> UserRecord | None:
        user = self.users.get(subject)
        return user if user and user.role == role else None


@lru_cache
def user_directory() -> UserDirectory:
    return UserDirectory.load(config.USER_DIRECTORY_PATH)
