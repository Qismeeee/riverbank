import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import llm
from llm import LlmClient, LlmError, LlmRequest, LlmResponse
from prompts import default_prompts
from services.statements import factory
from services.statements.classifiers import (
    GatedPayoutClassifier,
    JevPayoutClassifier,
    PatternPayoutClassifier,
)
from services.statements.documents import (
    CSV,
    PDF,
    PNG,
    StatementDocument,
    fold,
    parse_date,
    parse_vnd,
)
from services.statements.parsers import (
    ColumnMap,
    CsvStatementParser,
    PdfLayoutStatementParser,
    PdfTextStatementParser,
)
from services.statements.reader import StatementConfig, StatementReader
from services.statements.vision import VisionConfig, VisionStatementParser
from services.underwriting.models import WorkflowError
from tests.test_underwriting import (  # noqa: F401
    BANK,
    client,
    create,
    seller_headers,
    service,
)

FIXTURES = Path(__file__).parent / "fixtures" / "statements"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
HEADER = ["Ngày giao dịch", "Nội dung", "Ghi nợ", "Ghi có (VND)"]


def pdf_bytes(rows: list[list[str]]) -> bytes:
    xs, height, top = [40, 130, 390, 470, 570], 20, 760
    ops = []
    for r, row in enumerate(rows):
        y = top - (r + 1) * height
        for c, cell in enumerate(row):
            ops.append(f"{xs[c]} {y} {xs[c + 1] - xs[c]} {height} re S")
            if cell:
                ops.append(f"BT /F1 8 Tf {xs[c] + 3} {y + 6} Td ({cell}) Tj ET")
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def csv_bytes(rows: list[list[str]], delimiter: str = ";") -> bytes:
    return "\n".join(delimiter.join(row) for row in [HEADER, *rows]).encode("utf-8-sig")


class ScriptedVision(LlmClient):
    model = "vision-test"

    def __init__(self, *pages: dict):
        self.pages, self.requests = list(pages), []

    def complete_json(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(self.pages[len(self.requests) - 1], "vision-test-2026")


def ocr_line(day, desc, credit=None, debit=None) -> dict:
    return {"date": day, "raw_desc": desc, "credit_amount_vnd": credit, "debit_amount_vnd": debit}


def reader(*parsers, pattern=r"\bshopee\b") -> StatementReader:
    defaults = [CsvStatementParser(), PdfTextStatementParser()]
    return StatementReader(list(parsers) or defaults, PatternPayoutClassifier(pattern))


@pytest.mark.parametrize(
    "raw,value",
    [
        ("70.000.000", 70_000_000),
        ("70,000,000", 70_000_000),
        ("70.000.000,00", 70_000_000),
        ("1,500.50", 1_500),
        ("(2.000)", 2_000),
        ("", None),
        ("-", None),
        (12_000, 12_000),
    ],
)
def test_parse_vnd_reads_vietnamese_and_english_separators(raw, value):
    assert parse_vnd(raw) == value


def test_parse_date_and_fold():
    assert parse_date("05/10/2026 08:12") == "2026-10-05"
    assert parse_date("2026-10-05") == "2026-10-05"
    assert parse_date("31/02/2026") is None
    assert fold("  Ngày GIAO   dịch Đà ") == "ngay giao dich da"


def test_sniff_uses_magic_bytes_not_client_type():
    assert StatementDocument.sniff(b"%PDF-1.4 ...").media_type == PDF
    assert StatementDocument.sniff(PNG_BYTES).media_type == PNG
    assert StatementDocument.sniff("a;b\n".encode()).media_type == CSV
    with pytest.raises(WorkflowError) as exc:
        StatementDocument.sniff(b"\xff\xfe\x00\x81binary")
    assert (exc.value.code, exc.value.status) == ("STATEMENT_FORMAT_UNSUPPORTED", 415)


def test_csv_parser_maps_headers_and_skips_balance_rows():
    content = csv_bytes(
        [
            ["05/10/2026", "SHOPEE VN THANH TOAN DOANH THU", "", "70.000.000"],
            ["06/10/2026", "Chuyen tien mua hang", "5.000.000", ""],
            ["", "Số dư cuối kỳ", "", ""],
        ]
    )
    parsed = CsvStatementParser().parse(StatementDocument.sniff(content))
    assert [(x.date, x.credit, x.debit) for x in parsed.lines] == [
        ("2026-10-05", 70_000_000, None),
        ("2026-10-06", None, 5_000_000),
    ]


def test_csv_parser_reads_signed_amount_column():
    text = 'Date,Description,Amount\n2026-10-05,Shopee payout,"1,200,000"\n2026-10-06,Fee,-50000\n'
    lines = CsvStatementParser().parse(StatementDocument.sniff(text.encode())).lines
    assert [(x.credit, x.debit) for x in lines] == [(1_200_000, None), (None, 50_000)]


def test_pdf_text_layer_parses_table_without_model():
    content = pdf_bytes(
        [
            ["Ngay giao dich", "Noi dung", "Ghi no", "Ghi co"],
            ["05/10/2026", "SHOPEE VN THANH TOAN", "", "70.000.000"],
            ["06/10/2026", "Phi dich vu", "11.000", ""],
        ]
    )
    parsed = PdfTextStatementParser().parse(StatementDocument.sniff(content))
    assert parsed.parser == "pdf_text" and parsed.pages == 1
    assert [(x.date, x.raw_desc, x.credit, x.debit) for x in parsed.lines] == [
        ("2026-10-05", "SHOPEE VN THANH TOAN", 70_000_000, None),
        ("2026-10-06", "Phi dich vu", None, 11_000),
    ]


def test_scanned_pdf_falls_back_to_vision_page_by_page():
    vision_client = ScriptedVision(
        {"unreadable": False, "lines": [ocr_line("2026-10-05", "SHOPEE VN", 70_000_000)]}
    )
    vision = VisionStatementParser(vision_client, default_prompts().get("statement-ocr"))
    scanned = pdf_bytes([["", "", "", ""]])
    read = reader(CsvStatementParser(), PdfTextStatementParser(), vision).read(scanned)
    assert read.parsed.parser == "vision" and read.parsed.model == "vision-test-2026"
    assert read.parsed.prompt_version.startswith("statement-ocr@")
    request = vision_client.requests[0]
    assert request.images[0].startswith("data:image/png;base64,")
    assert request.user == {"page": 1, "pages": 1} and request.temperature == 0


def test_unreadable_document_lists_every_attempt():
    with pytest.raises(WorkflowError) as exc:
        reader().read(pdf_bytes([["", "", "", ""]]))
    assert exc.value.code == "STATEMENT_UNREADABLE"
    assert exc.value.details == [{"parser": "pdf_text", "notes": ["NO_TEXT_LAYER"]}]
    with pytest.raises(WorkflowError) as exc:
        reader().read(PNG_BYTES)
    assert (exc.value.code, exc.value.status) == ("STATEMENT_FORMAT_UNSUPPORTED", 415)


def test_vision_rejects_too_many_pages_and_maps_llm_errors():
    class Down(LlmClient):
        model = "down"

        def complete_json(self, request):
            raise LlmError("LLM_UNAVAILABLE")

    prompt = default_prompts().get("statement-ocr")
    with pytest.raises(WorkflowError) as exc:
        VisionStatementParser(Down(), prompt).parse(StatementDocument(PNG_BYTES, PNG))
    assert (exc.value.code, exc.value.status, exc.value.details) == (
        "STATEMENT_OCR_UNAVAILABLE",
        503,
        ["LLM_UNAVAILABLE"],
    )
    parser = VisionStatementParser(Down(), prompt, VisionConfig(max_pages=0))
    with pytest.raises(WorkflowError, match="STATEMENT_TOO_MANY_PAGES"):
        parser.parse(StatementDocument.sniff(pdf_bytes([["", "", "", ""]])))


def test_reader_limits_size_and_empty_files():
    small = StatementReader([CsvStatementParser()], PatternPayoutClassifier("x"))
    small.cfg = StatementConfig(max_bytes=4)
    with pytest.raises(WorkflowError) as exc:
        small.read(b"12345")
    assert (exc.value.code, exc.value.status) == ("STATEMENT_TOO_LARGE", 413)
    with pytest.raises(WorkflowError, match="STATEMENT_EMPTY"):
        small.read(b"")


def test_reconcile_sums_payout_credits_in_period_and_redacts_pii():
    content = csv_bytes(
        [
            ["05/10/2026", "SHOPEE VN THANH TOAN", "", "70.000.000"],
            ["12/10/2026", "Shopee payout tuan 41", "", "69.000.000"],
            ["13/10/2026", "Nguyen Van A chuyen tien 0912345678", "", "50.000.000"],
            ["14/10/2026", "SHOPEE VN hoan phi", "300.000", ""],
            ["20/10/2026", "SHOPEE VN THANH TOAN", "", "71.000.000"],
        ]
    )
    rb = reader()
    read = rb.read(content)
    result = rb.reconcile(read, date(2026, 10, 5), date(2026, 10, 19))
    assert result["marketplace_total"] == 139_000_000
    assert [r["is_payout"] for r in result["statement_lines"]] == [True, True, False, False, True]
    assert [r["in_period"] for r in result["statement_lines"]] == [True, True, True, True, False]
    assert "[REDACTED_PHONE]" in result["statement_lines"][2]["raw_desc"]
    assert result["document_ref"] == read.document.ref
    assert result["coverage"] == {"first_date": "2026-10-05", "last_date": "2026-10-20"}
    assert result["classifier"]["name"] == "pattern"


def test_reconcile_refuses_payout_lines_without_date_or_amount():
    vision_client = ScriptedVision(
        {
            "unreadable": False,
            "lines": [
                ocr_line("2026-10-05", "SHOPEE VN", 70_000_000),
                ocr_line(None, "SHOPEE VN", 1_000),
                ocr_line("2026-10-06", "SHOPEE VN", None),
            ],
        }
    )
    rb = reader(VisionStatementParser(vision_client, default_prompts().get("statement-ocr")))
    with pytest.raises(WorkflowError) as exc:
        rb.reconcile(rb.read(PNG_BYTES), date(2026, 10, 5), date(2026, 10, 19))
    assert (exc.value.code, exc.value.details) == ("STATEMENT_LINES_UNREADABLE", [1, 2])


def test_jev_classifier_batches_and_fails_closed():
    calls = []

    def decide(state, questions, timeout):
        calls.append(questions)
        return {
            name: {"noul": 0.9 if "SHOPEE" in q["instructions"] else 0.1}
            for name, q in questions.items()
        }

    labels = JevPayoutClassifier(batch_size=2, decide=decide).classify(
        ["SHOPEE VN", "ban be", "SHOPEE payout"]
    )
    assert [(x.is_payout, x.probability) for x in labels] == [
        (True, 0.9),
        (False, 0.1),
        (True, 0.9),
    ]
    assert [len(q) for q in calls] == [2, 1]

    def down(*args, **kwargs):
        raise TimeoutError

    with pytest.raises(WorkflowError) as exc:
        JevPayoutClassifier(decide=down).classify(["x"])
    assert (exc.value.code, exc.value.status) == ("STATEMENT_CLASSIFIER_UNAVAILABLE", 503)


def test_factory_validates_config(monkeypatch):
    assert [p.name for p in factory.build_parsers("off")] == ["csv", "pdf_text", "pdf_layout"]
    with pytest.raises(WorkflowError, match="STATEMENT_OCR_UNKNOWN"):
        factory.build_parsers("tesseract")
    with pytest.raises(WorkflowError, match="STATEMENT_CLASSIFIER_UNKNOWN"):
        factory.build_classifier("magic")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "STATEMENT_OCR_PROVIDER", "openai")
    with pytest.raises(WorkflowError, match="STATEMENT_OCR_NOT_CONFIGURED"):
        factory.build_parsers("llm")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "or-test")
    assert factory.build_classifier("jev").name == "pattern+jev"
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    with pytest.raises(WorkflowError, match="OPENROUTER_KEY_REQUIRED"):
        factory.build_classifier("jev")


def test_llm_client_sends_images_as_content_parts():
    llm_client = llm.OpenAICompatibleClient("sk-test", "m")
    calls = []
    reply = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"lines": []})))],
        model="m",
        usage=None,
    )
    llm_client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: calls.append(kw) or reply)
        )
    )
    llm_client.complete_json(
        LlmRequest("s", {"page": 1}, {}, "n", images=("data:image/png;base64,AA",))
    )
    content = calls[0]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": '{"page": 1}'}
    assert content[1]["image_url"]["url"] == "data:image/png;base64,AA"


@pytest.fixture
def upload_service(service):  # noqa: F811
    service.statements = reader()
    return service


def period_rows(case) -> tuple[str, str, list[str]]:
    weeks = case.runs[-1]["input_snapshot"]["features"]["weekly_payouts"]
    start = date.fromisoformat(weeks[-2]["week_start"])
    end = date.fromisoformat(weeks[-1]["week_start"]) + timedelta(days=7)
    inside = [(start + timedelta(days=d)).strftime("%d/%m/%Y") for d in (1, 8)]
    return start.isoformat(), end.isoformat(), inside


def upload(api, case, content, headers=BANK, version=None):
    start, end, _ = period_rows(case)
    return api.post(
        f"/v1/cases/{case.case_id}/statements/upload",
        headers={k: v for k, v in headers.items() if k != "Idempotency-Key"},
        data={
            "expected_version": str(version or case.version),
            "period_start": start,
            "period_end": end,
        },
        files={"file": ("saoke.csv", content, "text/csv")},
    )


@pytest.mark.parametrize(
    "second,status", [("49.000.000", "PENDING_REVIEW"), ("48.999.999", "ESCALATED")]
)
def test_uploaded_statement_drives_r05_through_existing_threshold(
    client,  # noqa: F811
    upload_service,
    second,
    status,
):
    case = upload_service.repository.get(create(client, "demo-c")["case_id"])
    _, _, (first_day, second_day) = period_rows(case)
    content = csv_bytes(
        [
            [first_day, "SHOPEE VN THANH TOAN", "", "70.000.000"],
            [second_day, "SHOPEE VN THANH TOAN", "", second],
            [second_day, "Ban be chuyen khoan", "", "40.000.000"],
        ]
    )
    response = upload(client, case, content, seller_headers("demo-c"))
    assert response.status_code == 200, response.text
    updated = upload_service.repository.get(case.case_id)
    assert updated.status == status and len(updated.runs) == 2
    statement = updated.runs[-1]["statement"]
    assert statement["source"] == "uploaded_file" and statement["submitted_by"] == "demo-c"
    assert statement["platform_total"] == 140_000_000
    assert statement["marketplace_total"] == 70_000_000 + parse_vnd(second)
    assert statement["extraction"]["parser"] == "csv"
    assert any(e["code"] == "E8" for e in updated.evidence)
    assert updated.events[-1]["event_type"] == "STATEMENT_RECORDED"


def test_upload_scope_version_and_state_checks(client, upload_service):  # noqa: F811
    case = upload_service.repository.get(create(client, "demo-c")["case_id"])
    content = csv_bytes([["05/10/2026", "SHOPEE VN", "", "1.000"]])
    assert upload(client, case, content, seller_headers("demo-b")).status_code == 403
    assert upload(client, case, content, version=case.version + 1).status_code == 409
    assert upload(client, case, b"\x00\x81\xff").status_code == 415
    ready = upload_service.repository.get(create(client, "demo-b")["case_id"])
    response = upload(client, ready, content)
    assert response.json()["detail"]["code"] == "STATEMENT_NOT_REQUESTED"
    assert upload_service.repository.get(case.case_id).version == case.version


def test_upload_without_reader_is_unavailable(client, service):  # noqa: F811
    case = service.repository.get(create(client, "demo-c")["case_id"])
    response = upload(client, case, csv_bytes([]))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "STATEMENT_READER_UNAVAILABLE"


@pytest.mark.parametrize(
    "header,columns",
    [
        (
            [
                "Ngày GD/Trans Date",
                "Nội dung/Description",
                "Số tiền ghi nợ/Debit",
                "Số tiền ghi có/Credit",
                "Số dư/Balance",
            ],
            {"date": 0, "desc": 1, "debit": 2, "credit": 3},
        ),
        (
            ["Ngày hiệu lực", "Diễn giải", "Phát sinh nợ", "Phát sinh có"],
            {"date": 0, "desc": 1, "debit": 2, "credit": 3},
        ),
        (
            ["STT", "Thời gian giao dịch", "Nội dung chuyển khoản", "Số tiền"],
            {"date": 1, "desc": 2, "amount": 3},
        ),
        (
            ["No.", "Date", "Description", "Debit", "Credit", "Balance"],
            {"date": 1, "desc": 2, "debit": 3, "credit": 4},
        ),
        (["STT", "Mô tả", "Nợ", "Có"], {"desc": 1, "debit": 2, "credit": 3}),
        (["Số dư", "Ghi chú", "Mã GD"], None),
    ],
)
def test_header_detection_covers_bank_variants(header, columns):
    found = ColumnMap.detect(header)
    assert (found.columns if found else None) == columns


def test_unlabelled_date_column_is_inferred_from_content():
    text = "STT;;Mô tả;Có\n1;05/10/2026;SHOPEE VN;70.000.000\n2;06/10/2026;Ban be;1.000\n"
    lines = CsvStatementParser().parse(StatementDocument.sniff(text.encode())).lines
    assert [(x.date, x.credit) for x in lines] == [
        ("2026-10-05", 70_000_000),
        ("2026-10-06", 1_000),
    ]


def jev_rejecting(*names: str):
    calls = []

    def decide(state, questions, timeout):
        calls.append(questions)
        return {
            key: {"noul": 0.05 if any(n in q["instructions"] for n in names) else 0.95}
            for key, q in questions.items()
        }

    return decide, calls


def test_gated_classifier_only_asks_jev_about_platform_candidates():
    decide, calls = jev_rejecting("NGUYEN VAN A")
    gated = GatedPayoutClassifier(
        PatternPayoutClassifier("shopee"), JevPayoutClassifier(decide=decide)
    )
    labels = gated.classify(
        ["SHOPEE VN quyet toan", "NGUYEN VAN A chuyen tien hang shopee", "Luong thang 9"]
    )
    assert [x.is_payout for x in labels] == [True, False, False]
    assert [len(q) for q in calls] == [2]


@pytest.mark.parametrize(
    "fixture,parser,pages,total",
    [
        ("bilingual_grid.pdf", "pdf_text", 2, 12 * (70_000_000 + 69_500_000)),
        ("unruled_text.pdf", "pdf_layout", 1, 70_000_000 + 69_500_000),
    ],
)
def test_vietnamese_pdf_fixtures_reconcile_without_model(fixture, parser, pages, total):
    decide, _ = jev_rejecting("NGUYEN VAN A")
    rb = StatementReader(
        [CsvStatementParser(), PdfTextStatementParser(), PdfLayoutStatementParser()],
        GatedPayoutClassifier(
            PatternPayoutClassifier("shopee"), JevPayoutClassifier(decide=decide)
        ),
    )
    read = rb.read((FIXTURES / fixture).read_bytes())
    result = rb.reconcile(read, date(2026, 10, 5), date(2026, 10, 19))
    assert (read.parsed.parser, read.parsed.pages) == (parser, pages)
    assert result["marketplace_total"] == total
    first = result["statement_lines"][0]
    assert first["raw_desc"].endswith("tuan 40 shop ABC") and first["debit"] is None


def test_layout_parser_reads_grid_pdf_across_pages_too():
    parsed = PdfLayoutStatementParser().parse(
        StatementDocument.sniff((FIXTURES / "bilingual_grid.pdf").read_bytes())
    )
    payouts = [x for x in parsed.lines if x.credit and x.date]
    assert len(payouts) == 36 and parsed.pages == 2


def test_statement_threshold_comes_from_policy_config(client, upload_service):  # noqa: F811
    upload_service.policy.cfg = replace(upload_service.policy.cfg, statement_mismatch_bps=0)
    case = upload_service.repository.get(create(client, "demo-c")["case_id"])
    _, _, (first_day, second_day) = period_rows(case)
    content = csv_bytes(
        [
            [first_day, "SHOPEE VN THANH TOAN", "", "70.000.000"],
            [second_day, "SHOPEE VN THANH TOAN", "", "69.999.999"],
        ]
    )
    assert upload(client, case, content).status_code == 200
    statement = upload_service.repository.get(case.case_id).runs[-1]["statement"]
    assert (statement["threshold_bps"], statement["matched"]) == (0, False)
