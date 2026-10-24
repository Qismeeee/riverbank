from dataclasses import dataclass, replace
from datetime import date

from services.guardrail import PiiRedactor
from services.underwriting.models import WorkflowError

from .classifiers import PayoutClassifier
from .documents import ParsedStatement, StatementDocument, within
from .parsers import StatementParser


@dataclass(frozen=True)
class StatementConfig:
    max_bytes: int = 10 * 1024 * 1024


@dataclass(frozen=True)
class ReadStatement:
    document: StatementDocument
    parsed: ParsedStatement


class StatementReader:
    def __init__(
        self,
        parsers: list[StatementParser],
        classifier: PayoutClassifier,
        redactor: PiiRedactor | None = None,
        cfg: StatementConfig | None = None,
    ):
        self.parsers, self.classifier = parsers, classifier
        self.redactor = redactor or PiiRedactor()
        self.cfg = cfg or StatementConfig()

    def read(self, content: bytes) -> ReadStatement:
        if not content:
            raise WorkflowError("STATEMENT_EMPTY")
        if len(content) > self.cfg.max_bytes:
            raise WorkflowError("STATEMENT_TOO_LARGE", 413, [{"max_bytes": self.cfg.max_bytes}])
        document = StatementDocument.sniff(content)
        candidates = [p for p in self.parsers if p.accepts(document)]
        if not candidates:
            raise WorkflowError("STATEMENT_FORMAT_UNSUPPORTED", 415, [document.media_type])
        attempts = []
        for parser in candidates:
            parsed = parser.parse(document)
            if parsed.lines:
                return ReadStatement(document, self._redact(parsed))
            attempts.append({"parser": parser.name, "notes": parsed.notes})
        raise WorkflowError("STATEMENT_UNREADABLE", 422, attempts)

    def _redact(self, parsed: ParsedStatement) -> ParsedStatement:
        return replace(
            parsed,
            lines=[
                replace(line, raw_desc=self.redactor.redact(line.raw_desc)) for line in parsed.lines
            ],
        )

    def reconcile(self, read: ReadStatement, start: date, end: date) -> dict:
        lines = read.parsed.lines
        inflows = [i for i, line in enumerate(lines) if not line.debit]
        labels = dict(
            zip(
                inflows,
                self.classifier.classify([lines[i].raw_desc for i in inflows]),
                strict=True,
            )
        )
        rows = []
        for index, line in enumerate(lines):
            label = labels.get(index)
            rows.append(
                {
                    "line_index": index,
                    "date": line.date,
                    "raw_desc": line.raw_desc,
                    "credit": line.credit,
                    "debit": line.debit,
                    "is_payout": bool(label and label.is_payout),
                    "probability": label.probability if label else None,
                    "in_period": within(line.date, start, end),
                }
            )
        unreadable = [
            r["line_index"]
            for r in rows
            if r["is_payout"] and (r["date"] is None or r["credit"] is None)
        ]
        if unreadable:
            raise WorkflowError("STATEMENT_LINES_UNREADABLE", 422, unreadable)
        dates = sorted(r["date"] for r in rows if r["date"])
        parsed = read.parsed
        return {
            "marketplace_total": sum(
                r["credit"] for r in rows if r["is_payout"] and r["in_period"]
            ),
            "document_ref": read.document.ref,
            "file": {"media_type": read.document.media_type, "pages": parsed.pages},
            "extraction": {
                "parser": parsed.parser,
                "model": parsed.model,
                "prompt_version": parsed.prompt_version,
                "notes": parsed.notes,
            },
            "classifier": {"name": self.classifier.name, "model": self.classifier.model},
            "coverage": {
                "first_date": dates[0] if dates else None,
                "last_date": dates[-1] if dates else None,
            },
            "statement_lines": rows,
        }
