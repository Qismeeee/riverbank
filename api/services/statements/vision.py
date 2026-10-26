import base64
import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from llm import LlmClient, LlmError, LlmRequest
from prompts import Prompt
from services.underwriting.models import WorkflowError

from .balance import check_balances
from .documents import (
    IMAGES,
    PDF,
    PNG,
    ParsedStatement,
    StatementDocument,
    StatementLine,
    StatementSummary,
    parse_date,
    parse_signed_vnd,
    parse_vnd,
)
from .images import ImageNormalizer
from .parsers import PageParser

_NULLABLE_INT = {"type": ["integer", "null"]}
OCR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["unreadable", "opening_balance_vnd", "closing_balance_vnd", "lines"],
    "properties": {
        "unreadable": {"type": "boolean"},
        "opening_balance_vnd": _NULLABLE_INT,
        "closing_balance_vnd": _NULLABLE_INT,
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "date",
                    "raw_desc",
                    "credit_amount_vnd",
                    "debit_amount_vnd",
                    "balance_vnd",
                ],
                "properties": {
                    "date": {"type": ["string", "null"]},
                    "raw_desc": {"type": "string"},
                    "credit_amount_vnd": _NULLABLE_INT,
                    "debit_amount_vnd": _NULLABLE_INT,
                    "balance_vnd": _NULLABLE_INT,
                },
            },
        },
    },
}


@dataclass(frozen=True)
class VisionConfig:
    max_pages: int = 10
    render_scale: float = 2.0
    retry_scale: float = 3.0
    workers: int = 4


@dataclass(frozen=True)
class PageResult:
    lines: list[StatementLine]
    unreadable: bool
    model: str
    summary: StatementSummary

    @property
    def mismatches(self) -> int:
        check = check_balances(self.lines, self.summary)
        return check.mismatches + (1 if check.totals and not check.totals.matched else 0)

    def better_than(self, other: "PageResult") -> bool:
        return self.mismatches < other.mismatches and len(self.lines) >= len(other.lines)


def _data_url(media_type: str, content: bytes) -> str:
    return f"data:{media_type};base64,{base64.b64encode(content).decode()}"


def page_count(document: StatementDocument) -> int:
    if document.media_type != PDF:
        return 1
    import pypdfium2

    pdf = pypdfium2.PdfDocument(document.content)
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_pages(
    document: StatementDocument,
    numbers: list[int],
    scale: float,
    images: ImageNormalizer | None = None,
) -> dict[int, str]:
    if document.media_type != PDF:
        url = _data_url(*(images or ImageNormalizer()).normalize(document.content))
        return {number: url for number in numbers}
    import pypdfium2

    pdf = pypdfium2.PdfDocument(document.content)
    try:
        urls = {}
        for number in numbers:
            buffer = io.BytesIO()
            pdf[number - 1].render(scale=scale).to_pil().save(buffer, format="PNG")
            urls[number] = _data_url(PNG, buffer.getvalue())
        return urls
    finally:
        pdf.close()


class VisionStatementParser(PageParser):
    name = "vision"
    media_types = frozenset({PDF, *IMAGES})

    def __init__(
        self,
        client: LlmClient,
        prompt: Prompt,
        cfg: VisionConfig | None = None,
        images: ImageNormalizer | None = None,
    ):
        self.client, self.prompt, self.cfg = client, prompt, cfg or VisionConfig()
        self.images = images or ImageNormalizer()

    def parse(self, document: StatementDocument) -> ParsedStatement:
        return self.parse_pages(document, tuple(range(1, page_count(document) + 1)))

    def parse_pages(self, document: StatementDocument, pages: tuple[int, ...]) -> ParsedStatement:
        if len(pages) > self.cfg.max_pages:
            raise WorkflowError(
                "STATEMENT_TOO_MANY_PAGES", 422, [{"max_pages": self.cfg.max_pages}]
            )
        total = page_count(document)
        results = self._read(document, list(pages), total, self.cfg.render_scale, False)
        notes: list[str] = []
        retry = [n for n in pages if results[n].mismatches]
        if retry:
            again = self._read(document, retry, total, self.cfg.retry_scale, True)
            for number in retry:
                notes.append(f"PAGE_{number}_RETRIED")
                if again[number].better_than(results[number]):
                    results[number] = again[number]
        notes += [f"PAGE_{n}_UNREADABLE" for n in pages if results[n].unreadable]
        lines = [line for n in pages for line in results[n].lines]
        model = results[pages[-1]].model if pages else self.client.model
        summary = StatementSummary()
        for number in pages:
            summary = summary.merge(results[number].summary)
        return ParsedStatement(
            self.name, lines, total, model, self.prompt.version, notes, summary=summary
        )

    def _read(
        self,
        document: StatementDocument,
        numbers: list[int],
        total: int,
        scale: float,
        retry: bool,
    ) -> dict[int, PageResult]:
        urls = render_pages(document, numbers, scale, self.images)
        with ThreadPoolExecutor(max_workers=self.cfg.workers) as pool:
            found = pool.map(lambda n: self._ask(urls[n], n, total, retry), numbers)
            return dict(zip(numbers, found, strict=True))

    def _ask(self, url: str, number: int, total: int, retry: bool) -> PageResult:
        user = {"page": number, "pages": total}
        if retry:
            user["retry_reason"] = "BALANCE_MISMATCH"
        try:
            response = self.client.complete_json(
                LlmRequest(
                    system=self.prompt.system,
                    user=user,
                    schema=OCR_SCHEMA,
                    schema_name="statement_lines",
                    temperature=self.prompt.temperature,
                    images=(url,),
                )
            )
        except LlmError as exc:
            raise WorkflowError("STATEMENT_OCR_UNAVAILABLE", 503, [exc.code]) from exc
        content = response.content
        lines = [self._line(item, number) for item in content.get("lines", [])]
        summary = StatementSummary(
            parse_signed_vnd(content.get("opening_balance_vnd")),
            parse_signed_vnd(content.get("closing_balance_vnd")),
        )
        return PageResult(lines, bool(content.get("unreadable")), response.model, summary)

    @staticmethod
    def _line(item: dict, page: int) -> StatementLine:
        return StatementLine(
            parse_date(item.get("date")),
            " ".join(str(item.get("raw_desc") or "").split()),
            parse_vnd(item.get("credit_amount_vnd")),
            parse_vnd(item.get("debit_amount_vnd")),
            parse_signed_vnd(item.get("balance_vnd")),
            page,
        )
