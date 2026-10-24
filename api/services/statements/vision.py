import base64
import io
from dataclasses import dataclass

from llm import LlmClient, LlmError, LlmRequest
from prompts import Prompt
from services.underwriting.models import WorkflowError

from .documents import (
    JPEG,
    PDF,
    PNG,
    ParsedStatement,
    StatementDocument,
    StatementLine,
    parse_date,
    parse_vnd,
)
from .parsers import StatementParser

_NULLABLE_INT = {"type": ["integer", "null"]}
OCR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["unreadable", "lines"],
    "properties": {
        "unreadable": {"type": "boolean"},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["date", "raw_desc", "credit_amount_vnd", "debit_amount_vnd"],
                "properties": {
                    "date": {"type": ["string", "null"]},
                    "raw_desc": {"type": "string"},
                    "credit_amount_vnd": _NULLABLE_INT,
                    "debit_amount_vnd": _NULLABLE_INT,
                },
            },
        },
    },
}


@dataclass(frozen=True)
class VisionConfig:
    max_pages: int = 10
    render_scale: float = 2.0


def _data_url(media_type: str, content: bytes) -> str:
    return f"data:{media_type};base64,{base64.b64encode(content).decode()}"


def render_pages(document: StatementDocument, cfg: VisionConfig) -> list[str]:
    if document.media_type != PDF:
        return [_data_url(document.media_type, document.content)]
    import pypdfium2

    pdf = pypdfium2.PdfDocument(document.content)
    try:
        if len(pdf) > cfg.max_pages:
            raise WorkflowError("STATEMENT_TOO_MANY_PAGES", 422, [{"max_pages": cfg.max_pages}])
        urls = []
        for page in pdf:
            buffer = io.BytesIO()
            page.render(scale=cfg.render_scale).to_pil().save(buffer, format="PNG")
            urls.append(_data_url(PNG, buffer.getvalue()))
        return urls
    finally:
        pdf.close()


class VisionStatementParser(StatementParser):
    name = "vision"
    media_types = frozenset({PDF, PNG, JPEG})

    def __init__(self, client: LlmClient, prompt: Prompt, cfg: VisionConfig | None = None):
        self.client, self.prompt, self.cfg = client, prompt, cfg or VisionConfig()

    def parse(self, document: StatementDocument) -> ParsedStatement:
        pages = render_pages(document, self.cfg)
        lines: list[StatementLine] = []
        notes: list[str] = []
        model = self.client.model
        for number, url in enumerate(pages, start=1):
            try:
                response = self.client.complete_json(
                    LlmRequest(
                        system=self.prompt.system,
                        user={"page": number, "pages": len(pages)},
                        schema=OCR_SCHEMA,
                        schema_name="statement_lines",
                        temperature=self.prompt.temperature,
                        images=(url,),
                    )
                )
            except LlmError as exc:
                raise WorkflowError("STATEMENT_OCR_UNAVAILABLE", 503, [exc.code]) from exc
            model = response.model
            if response.content.get("unreadable"):
                notes.append(f"PAGE_{number}_UNREADABLE")
            lines.extend(self._line(item) for item in response.content.get("lines", []))
        return ParsedStatement(self.name, lines, len(pages), model, self.prompt.version, notes)

    @staticmethod
    def _line(item: dict) -> StatementLine:
        return StatementLine(
            parse_date(item.get("date")),
            " ".join(str(item.get("raw_desc") or "").split()),
            parse_vnd(item.get("credit_amount_vnd")),
            parse_vnd(item.get("debit_amount_vnd")),
        )
