import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
OUT = Path(__file__).parent
pdfmetrics.registerFont(TTFont("Body", FONT))
TEXT = ParagraphStyle("t", fontName="Body", fontSize=7, leading=9)

PAYOUTS = [
    (
        "05/10/2026",
        "CONG TY TNHH SHOPEE thanh toan doanh thu don hang tuan 40 shop ABC",
        70_000_000,
    ),
    ("08/10/2026", "NGUYEN VAN A chuyen tien hang shopee", 15_000_000),
    ("12/10/2026", "SHOPEE VN IBFT quyet toan tuan 41", 69_500_000),
]
DEBITS = [("06/10/2026", "Thanh toan xuong may", 40_000_000), ("13/10/2026", "Phi SMS", 11_000)]


def _vnd(value: int, sep: str) -> str:
    return f"{value:,}".replace(",", sep)


def bilingual() -> None:
    header = [
        "STT",
        "Ngày GD/Trans Date",
        "Nội dung/Description",
        "Số tiền ghi nợ/Debit",
        "Số tiền ghi có/Credit",
        "Số dư/Balance",
    ]
    rows, balance = [], 10_000_000
    events = sorted(
        [(d, t, 0, a) for d, t, a in PAYOUTS] + [(d, t, a, 0) for d, t, a in DEBITS],
        key=lambda e: e[0][3:5] + e[0][:2],
    )
    for n, (day, desc, debit, credit) in enumerate(events * 12, start=1):
        balance += credit - debit
        rows.append(
            [
                str(n),
                day,
                Paragraph(desc, TEXT),
                _vnd(debit, ",") if debit else "",
                _vnd(credit, ",") if credit else "",
                _vnd(balance, ","),
            ]
        )
    total_credit = sum(a for *_, a in PAYOUTS) * 12
    rows.append(["", "", "Tổng cộng/Total", "", _vnd(total_credit, ","), ""])
    head = [Paragraph(h, TEXT) for h in header]
    table = Table([head, *rows], colWidths=[25, 60, 200, 80, 80, 80], repeatRows=0)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Body", 7),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ]
        )
    )
    story = [
        Paragraph("SAO KÊ TÀI KHOẢN / ACCOUNT STATEMENT", TEXT),
        Paragraph("Chủ tài khoản: NGUYEN THI B. Số tài khoản: 0123xxxxxx89", TEXT),
        Spacer(1, 12),
        table,
    ]
    SimpleDocTemplate(str(OUT / "bilingual_grid.pdf"), pagesize=A4).build(story)


def unruled() -> None:
    header = ["Ngày hiệu lực", "Diễn giải", "Phát sinh nợ", "Phát sinh có"]
    rows = [[d, t, "", _vnd(a, ".")] for d, t, a in PAYOUTS]
    rows += [[d, t, _vnd(a, "."), ""] for d, t, a in DEBITS]
    table = Table([header, *rows], colWidths=[70, 260, 80, 80])
    table.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Body", 8)]))
    SimpleDocTemplate(str(OUT / "unruled_text.pdf"), pagesize=A4).build([table])


if __name__ == "__main__":
    bilingual()
    unruled()
