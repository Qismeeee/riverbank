import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime

from services.underwriting.models import WorkflowError

CSV, PDF, PNG, JPEG = "text/csv", "application/pdf", "image/png", "image/jpeg"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SIGNATURES = ((b"%PDF-", PDF), (b"\x89PNG\r\n\x1a\n", PNG), (b"\xff\xd8\xff", JPEG))
ZIP_SIGNATURE, XLSX_PART = b"PK\x03\x04", "xl/workbook.xml"
DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y")
_DECIMAL_TAIL = re.compile(r"[.,]\d{1,2}$")
_NEGATIVE = re.compile(r"^\(.*\)$|^-|-$")


@dataclass(frozen=True)
class StatementDocument:
    content: bytes
    media_type: str

    @classmethod
    def sniff(cls, content: bytes) -> "StatementDocument":
        for signature, media_type in SIGNATURES:
            if content.startswith(signature):
                return cls(content, media_type)
        if content.startswith(ZIP_SIGNATURE):
            return cls._workbook(content)
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise WorkflowError("STATEMENT_FORMAT_UNSUPPORTED", 415) from exc
        return cls(content, CSV)

    @classmethod
    def _workbook(cls, content: bytes) -> "StatementDocument":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if XLSX_PART in archive.namelist():
                    return cls(content, XLSX)
        except zipfile.BadZipFile as exc:
            raise WorkflowError("STATEMENT_FORMAT_UNSUPPORTED", 415) from exc
        raise WorkflowError("STATEMENT_FORMAT_UNSUPPORTED", 415)

    @property
    def ref(self) -> str:
        return f"sha256:{hashlib.sha256(self.content).hexdigest()}"


@dataclass(frozen=True)
class StatementLine:
    date: str | None
    raw_desc: str
    credit: int | None
    debit: int | None


@dataclass(frozen=True)
class ParsedStatement:
    parser: str
    lines: list[StatementLine]
    pages: int = 0
    model: str | None = None
    prompt_version: str | None = None
    notes: list[str] = field(default_factory=list)


def fold(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(c for c in stripped if unicodedata.category(c) != "Mn")
    return " ".join(plain.lower().split())


def parse_vnd(text: object) -> int | None:
    if isinstance(text, int | float):
        return abs(int(text))
    raw = str(text or "").strip().replace(" ", "")
    if not raw or not any(c.isdigit() for c in raw):
        return None
    digits = re.sub(r"\D", "", _DECIMAL_TAIL.sub("", raw))
    return int(digits) if digits else None


def is_negative(text: object) -> bool:
    return bool(_NEGATIVE.search(str(text or "").strip()))


def parse_date(text: object) -> str | None:
    if isinstance(text, datetime):
        return text.date().isoformat()
    if isinstance(text, date):
        return text.isoformat()
    head = str(text or "").strip().split(" ")[0]
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(head, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def within(day: str | None, start: date, end: date) -> bool:
    return day is not None and start <= date.fromisoformat(day) < end
