import csv
import io
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .documents import (
    CSV,
    PDF,
    ParsedStatement,
    StatementDocument,
    StatementLine,
    fold,
    is_negative,
    parse_date,
    parse_vnd,
)

HEADER_KEYWORDS = (
    ("balance", ("so du", "balance")),
    ("credit", ("ghi co", "phat sinh co", "so tien co", "tien vao", "credit", "co")),
    ("debit", ("ghi no", "phat sinh no", "so tien no", "tien ra", "debit", "no")),
    ("date", ("ngay", "thoi gian", "date", "time")),
    ("desc", ("noi dung", "dien giai", "mo ta", "chi tiet", "description", "details", "remark")),
    ("amount", ("so tien", "amount")),
)
SHORT_KEYWORDS = frozenset({"co", "no"})
DATE_COLUMN_SHARE = 0.8
MONEY_ROLES = frozenset({"credit", "debit", "amount", "balance"})
_NUMERIC = re.compile(r"^[(+-]?[\d.,]*\d[\d.,]*[)-]?$")
_SEGMENTS = re.compile(r"[/|\n]")
_NOISE = re.compile(r"\(.*?\)|[^a-z0-9 ]")


def _segments(cell: object) -> list[str]:
    parts = _SEGMENTS.split(fold(str(cell or "")))
    return [" ".join(_NOISE.sub(" ", part).split()) for part in parts if part.strip()]


def _has_phrase(segment: str, phrase: str) -> bool:
    if phrase in SHORT_KEYWORDS:
        return segment == phrase
    return f" {phrase} " in f" {segment} "


def header_role(cell: object) -> tuple[str, int] | None:
    segments = _segments(cell)
    for role, phrases in HEADER_KEYWORDS:
        matched = [len(p) for seg in segments for p in phrases if _has_phrase(seg, p)]
        if matched:
            return role, max(matched)
    return None


@dataclass(frozen=True)
class ColumnMap:
    columns: dict[str, int]

    @classmethod
    def detect(cls, row: list) -> "ColumnMap | None":
        best: dict[str, tuple[int, int]] = {}
        for index, cell in enumerate(row):
            if (found := header_role(cell)) and found[1] > best.get(found[0], (0, 0))[0]:
                best[found[0]] = (found[1], index)
        columns = {role: index for role, (_, index) in best.items() if role != "balance"}
        if "desc" not in columns or not ({"credit", "amount"} & columns.keys()):
            return None
        return cls(columns)

    def with_date_from(self, rows: list[list]) -> "ColumnMap":
        if "date" in self.columns or not rows:
            return self
        taken = set(self.columns.values())
        for index in range(max(len(r) for r in rows)):
            cells = [r[index] for r in rows if index < len(r) and str(r[index] or "").strip()]
            dated = [c for c in cells if parse_date(c)]
            if index not in taken and cells and len(dated) >= DATE_COLUMN_SHARE * len(cells):
                return ColumnMap({**self.columns, "date": index})
        return self

    def _cell(self, row: list, role: str) -> object:
        index = self.columns.get(role)
        return row[index] if index is not None and index < len(row) else None

    def fits(self, row: list) -> bool:
        return len(row) > max(self.columns.values())

    def line(self, row: list) -> StatementLine | None:
        credit, debit = parse_vnd(self._cell(row, "credit")), parse_vnd(self._cell(row, "debit"))
        amount = self._cell(row, "amount")
        if (value := parse_vnd(amount)) is not None:
            credit, debit = (None, value) if is_negative(amount) else (value, None)
        if not credit and not debit:
            return None
        desc = " ".join(str(self._cell(row, "desc") or "").split())
        return StatementLine(parse_date(self._cell(row, "date")), desc, credit, debit)


class StatementParser(ABC):
    name: str
    media_types: frozenset[str]

    def accepts(self, document: StatementDocument) -> bool:
        return document.media_type in self.media_types

    @abstractmethod
    def parse(self, document: StatementDocument) -> ParsedStatement: ...


class TabularStatementParser(StatementParser):
    @staticmethod
    def lines(tables: list[list[list]]) -> list[StatementLine]:
        found: list[StatementLine] = []
        mapping: ColumnMap | None = None
        body: list[list] = []
        for table in tables:
            for row in table:
                detected = ColumnMap.detect(row)
                if detected:
                    found.extend(TabularStatementParser._rows(mapping, body))
                    mapping, body = detected, []
                elif mapping and mapping.fits(row):
                    body.append(row)
        return found + TabularStatementParser._rows(mapping, body)

    @staticmethod
    def _rows(mapping: ColumnMap | None, body: list[list]) -> list[StatementLine]:
        if not mapping:
            return []
        mapping = mapping.with_date_from(body)
        return [line for row in body if (line := mapping.line(row))]


class CsvStatementParser(TabularStatementParser):
    name = "csv"
    media_types = frozenset({CSV})

    def parse(self, document: StatementDocument) -> ParsedStatement:
        text = document.content.decode("utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
        return ParsedStatement(self.name, self.lines([rows]))


class PdfTextStatementParser(TabularStatementParser):
    name = "pdf_text"
    media_types = frozenset({PDF})

    def parse(self, document: StatementDocument) -> ParsedStatement:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(document.content)) as pdf:
            pages = len(pdf.pages)
            if not any(page.chars for page in pdf.pages):
                return ParsedStatement(self.name, [], pages, notes=["NO_TEXT_LAYER"])
            tables = [t for page in pdf.pages for t in page.extract_tables()]
        if found := self.lines(tables):
            return ParsedStatement(self.name, found, pages)
        return ParsedStatement(self.name, [], pages, notes=["NO_RULED_TABLE"])


@dataclass(frozen=True)
class LayoutConfig:
    line_tolerance: float = 3.0
    cell_gap: float = 6.0
    attach_distance: float = 24.0


@dataclass(frozen=True)
class _Column:
    text: str
    role: str | None
    center: float


@dataclass
class _Row:
    page: int
    top: float
    cells: list[list[str]]
    anchored: bool


class PdfLayoutStatementParser(TabularStatementParser):
    name = "pdf_layout"
    media_types = frozenset({PDF})

    def __init__(self, cfg: LayoutConfig | None = None):
        self.cfg = cfg or LayoutConfig()

    def parse(self, document: StatementDocument) -> ParsedStatement:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(document.content)) as pdf:
            pages = len(pdf.pages)
            lines = [
                (number, line)
                for number, page in enumerate(pdf.pages)
                for line in self._lines(page.extract_words())
            ]
        if not lines:
            return ParsedStatement(self.name, [], pages, notes=["NO_TEXT_LAYER"])
        if found := self.lines(self._tables(lines)):
            return ParsedStatement(self.name, found, pages)
        return ParsedStatement(self.name, [], pages, notes=["NO_STATEMENT_HEADER"])

    def _lines(self, words: list[dict]) -> list[list[dict]]:
        rows: list[list[dict]] = []
        for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
            if rows and abs(rows[-1][0]["top"] - word["top"]) <= self.cfg.line_tolerance:
                rows[-1].append(word)
            else:
                rows.append([word])
        return [sorted(row, key=lambda w: w["x0"]) for row in rows]

    def _cells(self, line: list[dict]) -> list[list[dict]]:
        cells = [[line[0]]]
        for word in line[1:]:
            if word["x0"] - cells[-1][-1]["x1"] > self.cfg.cell_gap:
                cells.append([word])
            else:
                cells[-1].append(word)
        return cells

    @staticmethod
    def _text(cell: list[dict]) -> str:
        return " ".join(w["text"] for w in cell)

    def _header(self, line: list[dict]) -> list[_Column] | None:
        cells = self._cells(line)
        texts = [self._text(cell) for cell in cells]
        if not ColumnMap.detect(texts):
            return None
        return [
            _Column(text, (header_role(text) or (None,))[0], (c[0]["x0"] + c[-1]["x1"]) / 2)
            for text, c in zip(texts, cells, strict=True)
        ]

    @staticmethod
    def _nearest(columns: list[_Column], x: float, allowed) -> int | None:
        options = [i for i, c in enumerate(columns) if allowed(c)]
        return min(options, key=lambda i: abs(columns[i].center - x)) if options else None

    def _row(self, page: int, line: list[dict], columns: list[_Column]) -> _Row:
        cells: list[list[str]] = [[] for _ in columns]
        anchored = False
        for cell in self._cells(line):
            x, text = (cell[0]["x0"] + cell[-1]["x1"]) / 2, self._text(cell)
            if parse_date(text):
                target = self._nearest(columns, x, lambda c: c.role == "date")
                anchored = anchored or target is not None
            elif _NUMERIC.match(text):
                target = self._nearest(columns, x, lambda c: c.role != "desc")
                anchored = anchored or columns[target].role in MONEY_ROLES
            else:
                target = self._nearest(columns, x, lambda c: c.role not in MONEY_ROLES)
            if target is not None:
                cells[target].append(text)
        return _Row(page, line[0]["top"], cells, anchored)

    def _attach(self, rows: list[_Row]) -> list[list[str]]:
        anchors = [r for r in rows if r.anchored]
        for loose in (r for r in rows if not r.anchored):
            near = [
                a
                for a in anchors
                if a.page == loose.page and abs(a.top - loose.top) <= self.cfg.attach_distance
            ]
            if not near:
                continue
            target = min(near, key=lambda a: abs(a.top - loose.top))
            for i, words in enumerate(loose.cells):
                target.cells[i] = (
                    words + target.cells[i] if loose.top < target.top else (target.cells[i] + words)
                )
        return [[" ".join(cell) for cell in a.cells] for a in anchors]

    def _tables(self, lines: list[tuple[int, list[dict]]]) -> list[list[list[str]]]:
        tables: list[list[list[str]]] = []
        columns: list[_Column] | None = None
        rows: list[_Row] = []
        for page, line in lines:
            if header := self._header(line):
                if columns and rows:
                    tables.append([[c.text for c in columns], *self._attach(rows)])
                columns, rows = header, []
            elif columns:
                rows.append(self._row(page, line, columns))
        if columns:
            tables.append([[c.text for c in columns], *self._attach(rows)])
        return tables
