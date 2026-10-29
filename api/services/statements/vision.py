import base64
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from threading import Lock

from llm import LlmClient, LlmError, LlmRequest
from logs import stdout_logger
from prompts import Prompt
from services.metering import record_usage
from services.underwriting.models import WorkflowError

from .balance import check_balances
from .documents import (
    IMAGES,
    JPEG,
    PDF,
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

log = stdout_logger("riverbank.ocr")
_PDFIUM = Lock()
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
    workers: int = 8
    jpeg_quality: int = 90


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


@dataclass(frozen=True)
class PageRead:
    result: PageResult
    retried: bool


def _data_url(media_type: str, content: bytes) -> str:
    return f"data:{media_type};base64,{base64.b64encode(content).decode()}"


def page_count(document: StatementDocument) -> int:
    if document.media_type != PDF:
        return 1
    import pypdfium2

    with _PDFIUM:
        pdf = pypdfium2.PdfDocument(document.content)
        try:
            return len(pdf)
        finally:
            pdf.close()


class PageRenderer:
    def __init__(self, document: StatementDocument, images: ImageNormalizer, quality: int):
        self.document, self.images, self.quality = document, images, quality
        self._photo: str | None = None

    def url(self, number: int, scale: float) -> str:
        if self.document.media_type != PDF:
            if self._photo is None:
                self._photo = _data_url(*self.images.normalize(self.document.content))
            return self._photo
        import pypdfium2

        with _PDFIUM:
            pdf = pypdfium2.PdfDocument(self.document.content)
            try:
                image = pdf[number - 1].render(scale=scale).to_pil()
            finally:
                pdf.close()
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=self.quality)
        return _data_url(JPEG, buffer.getvalue())


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
        started = time.perf_counter()
        total = page_count(document)
        reads = self._read(PageRenderer(document, self.images, self.cfg.jpeg_quality), pages, total)
        retried = [n for n in pages if reads[n].retried]
        notes = [f"PAGE_{n}_RETRIED" for n in retried]
        notes += [f"PAGE_{n}_UNREADABLE" for n in pages if reads[n].result.unreadable]
        lines = [line for n in pages for line in reads[n].result.lines]
        model = reads[pages[-1]].result.model if pages else self.client.model
        summary = StatementSummary()
        for number in pages:
            summary = summary.merge(reads[number].result.summary)
        self._log("ocr_document", pages=len(pages), retried=len(retried), started=started)
        return ParsedStatement(
            self.name, lines, total, model, self.prompt.version, notes, summary=summary
        )

    def _read(
        self, renderer: PageRenderer, pages: tuple[int, ...], total: int
    ) -> dict[int, PageRead]:
        jobs = [(number, copy_context()) for number in pages]
        workers = max(1, min(self.cfg.workers, len(pages)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            found = pool.map(lambda job: job[1].run(self._page, renderer, job[0], total), jobs)
            return dict(zip(pages, found, strict=True))

    def _page(self, renderer: PageRenderer, number: int, total: int) -> PageRead:
        started = time.perf_counter()
        first = self._ask(renderer.url(number, self.cfg.render_scale), number, total, False)
        result, retried = first, False
        if first.mismatches:
            again = self._ask(renderer.url(number, self.cfg.retry_scale), number, total, True)
            result, retried = (again if again.better_than(first) else first), True
        self._log("ocr_page", page=number, retried=retried, started=started)
        return PageRead(result, retried)

    def _log(self, event: str, started: float, **fields) -> None:
        seconds = round(time.perf_counter() - started, 2)
        record = {"event": event, "model": self.client.model, **fields, "seconds": seconds}
        log.info(json.dumps(record))

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
        record_usage(
            "statement_ocr",
            getattr(self.client, "provider", "openai"),
            response.model,
            response.usage,
            response.cached,
        )
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
