READING_FIELDS = (
    "source",
    "document_ref",
    "submitted_by",
    "period_start",
    "period_end",
    "file",
    "extraction",
    "classifier",
    "coverage",
    "statement_lines",
    "marketplace_total",
    "platform_total",
    "difference",
    "matched",
    "message",
    "threshold_bps",
)


def latest_uploaded_statement(case) -> dict | None:
    for run in reversed(case.runs):
        statement = run.get("statement") or {}
        if statement.get("source") == "uploaded_file":
            return statement
    return None


def reading_payload(statement: dict) -> dict:
    return {key: statement[key] for key in READING_FIELDS if key in statement}


def file_summary(statement: dict) -> dict:
    file = statement.get("file") or {}
    extraction = statement.get("extraction") or {}
    original = statement.get("original") or {}
    pages = file.get("pages")
    parser = extraction.get("parser")
    lines = statement.get("statement_lines")
    return {
        "media_type": file.get("media_type"),
        "pages": pages if isinstance(pages, int) else None,
        "parser": parser if isinstance(parser, str) else None,
        "lines": len(lines) if isinstance(lines, list) else None,
        "stored": original.get("store") not in (None, "none"),
    }
