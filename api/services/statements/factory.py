import config
from llm import LlmError, build_llm_client
from prompts import default_prompts
from services.underwriting.models import WorkflowError

from .classifiers import (
    GatedPayoutClassifier,
    JevPayoutClassifier,
    PatternPayoutClassifier,
    PayoutClassifier,
)
from .parsers import (
    CsvStatementParser,
    ExcelStatementParser,
    PdfLayoutStatementParser,
    PdfTextStatementParser,
    StatementParser,
)
from .reader import StatementReader
from .vision import VisionStatementParser

OCR_MODES = ("off", "llm")


def build_classifier(kind: str) -> PayoutClassifier:
    if kind == "pattern":
        return PatternPayoutClassifier(config.STATEMENT_PAYOUT_PATTERN)
    if kind == "jev":
        if not config.OPENROUTER_API_KEY:
            raise WorkflowError("OPENROUTER_KEY_REQUIRED", 503)
        return GatedPayoutClassifier(
            PatternPayoutClassifier(config.STATEMENT_PAYOUT_PATTERN), JevPayoutClassifier()
        )
    raise WorkflowError("STATEMENT_CLASSIFIER_UNKNOWN", 503)


def build_parsers(ocr: str) -> list[StatementParser]:
    if ocr not in OCR_MODES:
        raise WorkflowError("STATEMENT_OCR_UNKNOWN", 503)
    parsers: list[StatementParser] = [
        CsvStatementParser(),
        ExcelStatementParser(),
        PdfTextStatementParser(),
        PdfLayoutStatementParser(),
    ]
    if ocr == "llm":
        try:
            client = build_llm_client(
                "statement_ocr", config.STATEMENT_OCR_PROVIDER, config.STATEMENT_OCR_MODEL
            )
        except LlmError as exc:
            raise WorkflowError("STATEMENT_OCR_NOT_CONFIGURED", 503, [exc.code]) from exc
        parsers.append(VisionStatementParser(client, default_prompts().get("statement-ocr")))
    return parsers


def build_statement_reader() -> StatementReader:
    return StatementReader(
        build_parsers(config.STATEMENT_OCR), build_classifier(config.STATEMENT_CLASSIFIER)
    )
