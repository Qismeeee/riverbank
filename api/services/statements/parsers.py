import csv
import io
import re
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from itertools import islice

from .documents import (
    CSV,
    PDF,
    XLS,
    XLSX,
    ParsedStatement,
    StatementDocument,
    StatementLine,
    StatementSummary,
    fold,
    is_negative,
    parse_date,
    parse_signed_vnd,
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
SUMMARY_KEYWORDS = (
    ("opening", ("so du dau ky", "so du dau", "opening balance", "beginning balance")),
    ("closing", ("so du cuoi ky", "so du cuoi", "closing balance", "ending balance")),
    ("total", ("tong cong", "cong phat sinh", "tong phat sinh", "total")),
)
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


def _is_amount(cell: object) -> bool:
    if isinstance(cell, bool):
        return False
    return isinstance(cell, int | float) or bool(_NUMERIC.match(str(cell or "").strip()))


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
    balance: int | None = None

    @classmethod
    def detect(cls, row: list) -> "ColumnMap | None":
        best: dict[str, tuple[int, int]] = {}
        for index, cell in enumerate(row):
            if (found := header_role(cell)) and found[1] > best.get(found[0], (0, 0))[0]:
                best[found[0]] = (found[1], index)
        columns = {role: index for role, (_, index) in best.items() if role != "balance"}
        if "desc" not in columns or not ({"credit", "amount"} & columns.keys()):
            return None
        return cls(columns, best["balance"][1] if "balance" in best else None)

    def with_date_from(self, rows: list[list]) -> "ColumnMap":
        if "date" in self.columns or not rows:
            return self
        taken = {*self.columns.values(), self.balance}
        for index in range(max(len(r) for r in rows)):
            cells = [r[index] for r in rows if index < len(r) and str(r[index] or "").strip()]
            dated = [c for c in cells if parse_date(c)]
            if index not in taken and cells and len(dated) >= DATE_COLUMN_SHARE * len(cells):
                return ColumnMap({**self.columns, "date": index}, self.balance)
        return self

    def _cell(self, row: list, role: str) -> object:
        index = self.columns.get(role)
        return row[index] if index is not None and index < len(row) else None

    def fits(self, row: list) -> bool:
        return len(row) > max(self.columns.values())

    def summary(self, row: list) -> tuple[str, int | None] | None:
        text = fold(" ".join(str(cell) for cell in row if cell is not None))
        kind = next((k for k, phrases in SUMMARY_KEYWORDS if any(p in text for p in phrases)), None)
        if kind is None:
            return None
        preferred = (
            [row[self.balance]] if self.balance is not None and self.balance < len(row) else []
        )
        numbers = [c for c in [*preferred, *reversed(row)] if _is_amount(c)]
        return kind, parse_signed_vnd(numbers[0]) if numbers else None

    def line(self, row: list) -> StatementLine | None:
        credit, debit = parse_vnd(self._cell(row, "credit")), parse_vnd(self._cell(row, "debit"))
        amount = self._cell(row, "amount")
        if (value := parse_vnd(amount)) is not None:
            credit, debit = (None, value) if is_negative(amount) else (value, None)
        if not credit and not debit:
            return None
        desc = " ".join(str(self._cell(row, "desc") or "").split())
        balance = self.balance is not None and self.balance < len(row)
        return StatementLine(
            parse_date(self._cell(row, "date")),
            desc,
            credit,
            debit,
            parse_signed_vnd(row[self.balance]) if balance else None,
        )


class StatementParser(ABC):
    name: str
    media_types: frozenset[str]

    def accepts(self, document: StatementDocument) -> bool:
        return document.media_type in self.media_types

    @abstractmethod
    def parse(self, document: StatementDocument) -> ParsedStatement: ...


class PageParser(StatementParser):
    @abstractmethod
    def parse_pages(
        self, document: StatementDocument, pages: tuple[int, ...]
    ) -> ParsedStatement: ...


@dataclass(frozen=True)
class TableScan:
    lines: list[StatementLine]
    summary: StatementSummary

    def parsed(self, name: str, pages: int = 0, **extra) -> ParsedStatement:
        return ParsedStatement(name, self.lines, pages, summary=self.summary, **extra)


class TabularStatementParser(StatementParser):
    @staticmethod
    def scan(tables: list[list[list]], pages: list[list[int | None]] | None = None) -> TableScan:
        lines: list[StatementLine] = []
        summary = StatementSummary()
        mapping: ColumnMap | None = None
        body: list[tuple[int | None, list]] = []
        for table, table_pages in zip(
            tables, pages or [[None] * len(t) for t in tables], strict=True
        ):
            for row, page in zip(table, table_pages, strict=True):
                detected = ColumnMap.detect(row)
                if detected:
                    found, summary = TabularStatementParser._rows(mapping, body, summary)
                    lines.extend(found)
                    mapping, body = detected, []
                elif mapping and mapping.fits(row):
                    body.append((page, row))
        found, summary = TabularStatementParser._rows(mapping, body, summary)
        return TableScan(lines + found, summary)

    @staticmethod
    def _rows(
        mapping: ColumnMap | None,
        body: list[tuple[int | None, list]],
        summary: StatementSummary,
    ) -> tuple[list[StatementLine], StatementSummary]:
        if not mapping:
            return [], summary
        mapping = mapping.with_date_from([row for _, row in body])
        lines = []
        for page, row in body:
            found = mapping.summary(row)
            if found is None:
                if line := mapping.line(row):
                    lines.append(replace(line, page=page))
            elif found[0] == "opening" and summary.opening is None:
                summary = StatementSummary(found[1], summary.closing)
            elif found[0] == "closing" and found[1] is not None:
                summary = StatementSummary(summary.opening, found[1])
        return lines, summary


@dataclass(frozen=True)
class PdfPageConfig:
    defer_images: bool = False
    image_share: float = 0.5


class PdfStatementParser(TabularStatementParser):
    media_types = frozenset({PDF})

    def __init__(self, pages: PdfPageConfig | None = None):
        self.pages = pages or PdfPageConfig()

    def image_pages(self, pages: list) -> tuple[int, ...]:
        if not self.pages.defer_images:
            return ()
        return tuple(page.page_number for page in pages if self._is_image_page(page))

    def _is_image_page(self, page) -> bool:
        if not page.chars:
            return True
        if self._image_share(page) < self.pages.image_share:
            return False
        return not any(parse_date(word["text"]) for word in page.extract_words())

    @staticmethod
    def _image_share(page) -> float:
        covered = sum(
            max(0.0, min(i["x1"], page.width) - max(i["x0"], 0))
            * max(0.0, min(i["bottom"], page.height) - max(i["top"], 0))
            for i in page.images
        )
        return covered / (page.width * page.height)


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
        return self.scan([rows]).parsed(self.name)


class WorkbookStatementParser(TabularStatementParser):
    def __init__(self, max_rows: int = 50_000):
        self.max_rows = max_rows

    @abstractmethod
    def sheets(self, content: bytes) -> list[list[list]]: ...

    def parse(self, document: StatementDocument) -> ParsedStatement:
        try:
            tables = self.sheets(document.content)
        except WorkbookUnreadable:
            return ParsedStatement(self.name, [], notes=["WORKBOOK_UNREADABLE"])
        scan = self.scan(tables)
        if scan.lines:
            return scan.parsed(self.name, len(tables))
        return ParsedStatement(self.name, [], len(tables), notes=["NO_STATEMENT_HEADER"])


class WorkbookUnreadable(Exception):
    pass


class ExcelStatementParser(WorkbookStatementParser):
    name = "xlsx"
    media_types = frozenset({XLSX})

    def sheets(self, content: bytes) -> list[list[list]]:
        import openpyxl
        from openpyxl.utils.exceptions import InvalidFileException

        try:
            book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except (InvalidFileException, zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
            raise WorkbookUnreadable from exc
        try:
            return [
                [list(row) for row in islice(sheet.iter_rows(values_only=True), self.max_rows)]
                for sheet in book.worksheets
            ]
        finally:
            book.close()


class LegacyExcelStatementParser(WorkbookStatementParser):
    name = "xls"
    media_types = frozenset({XLS})

    def sheets(self, content: bytes) -> list[list[list]]:
        import xlrd

        try:
            book = xlrd.open_workbook(file_contents=content)
        except (xlrd.XLRDError, xlrd.compdoc.CompDocError, ValueError) as exc:
            raise WorkbookUnreadable from exc
        return [
            [
                [self._value(cell, book.datemode) for cell in sheet.row(index)]
                for index in range(min(sheet.nrows, self.max_rows))
            ]
            for sheet in book.sheets()
        ]

    @staticmethod
    def _value(cell, datemode: int) -> object:
        import xlrd

        if cell.ctype == xlrd.XL_CELL_DATE:
            return xlrd.xldate_as_datetime(cell.value, datemode)
        if cell.ctype == xlrd.XL_CELL_EMPTY:
            return None
        return cell.value


class PdfTextStatementParser(PdfStatementParser):
    name = "pdf_text"

    def parse(self, document: StatementDocument) -> ParsedStatement:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(document.content)) as pdf:
            pages = len(pdf.pages)
            if not any(page.chars for page in pdf.pages):
                return ParsedStatement(self.name, [], pages, notes=["NO_TEXT_LAYER"])
            deferred = self.image_pages(pdf.pages)
            tables, table_pages = [], []
            for page in pdf.pages:
                if page.page_number in deferred:
                    continue
                for table in page.extract_tables():
                    tables.append(table)
                    table_pages.append([page.page_number] * len(table))
        scan = self.scan(tables, table_pages)
        if scan.lines:
            return scan.parsed(self.name, pages, image_pages=deferred)
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


class PdfLayoutStatementParser(PdfStatementParser):
    name = "pdf_layout"

    def __init__(self, cfg: LayoutConfig | None = None, pages: PdfPageConfig | None = None):
        super().__init__(pages)
        self.cfg = cfg or LayoutConfig()

    def parse(self, document: StatementDocument) -> ParsedStatement:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(document.content)) as pdf:
            pages = len(pdf.pages)
            if not any(page.chars for page in pdf.pages):
                return ParsedStatement(self.name, [], pages, notes=["NO_TEXT_LAYER"])
            deferred = self.image_pages(pdf.pages)
            lines = [
                (page.page_number, line)
                for page in pdf.pages
                if page.page_number not in deferred
                for line in self._lines(page.extract_words())
            ]
        scan = self.scan(*self._tables(lines))
        if scan.lines:
            return scan.parsed(self.name, pages, image_pages=deferred)
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

    def _attach(self, rows: list[_Row]) -> list[tuple[int, list[str]]]:
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
        return [(a.page, [" ".join(cell) for cell in a.cells]) for a in anchors]

    def _tables(
        self, lines: list[tuple[int, list[dict]]]
    ) -> tuple[list[list[list[str]]], list[list[int | None]]]:
        tables: list[list[list[str]]] = []
        pages: list[list[int | None]] = []
        columns: list[_Column] | None = None
        rows: list[_Row] = []

        def close() -> None:
            attached = self._attach(rows)
            tables.append([[c.text for c in columns], *(cells for _, cells in attached)])
            pages.append([None, *(page for page, _ in attached)])

        for page, line in lines:
            if header := self._header(line):
                if columns and rows:
                    close()
                columns, rows = header, []
            elif columns:
                rows.append(self._row(page, line, columns))
        if columns:
            close()
        return tables, pages
