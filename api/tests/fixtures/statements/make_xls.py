from datetime import date
from pathlib import Path

import xlwt

OUT = Path(__file__).parent / "legacy.xls"
OPENING = 10_000_000
EVENTS = [
    (date(2026, 10, 5), "CONG TY TNHH SHOPEE thanh toan doanh thu tuan 40", 0, 70_000_000),
    (date(2026, 10, 6), "Thanh toan xuong may", 40_000_000, 0),
    (date(2026, 10, 12), "SHOPEE VN IBFT quyet toan tuan 41", 0, 69_500_000),
    (date(2026, 10, 13), "Phi SMS", 11_000, 0),
]


def legacy() -> None:
    book = xlwt.Workbook()
    sheet = book.add_sheet("Sao ke")
    day = xlwt.easyxf(num_format_str="DD/MM/YYYY")
    sheet.write(0, 0, "SAO KE TAI KHOAN")
    for col, text in enumerate(["Ngày giao dịch", "Nội dung", "Ghi nợ", "Ghi có", "Số dư"]):
        sheet.write(1, col, text)
    sheet.write(2, 1, "Số dư đầu kỳ")
    sheet.write(2, 4, OPENING)
    balance, row = OPENING, 3
    for when, desc, debit, credit in EVENTS:
        balance += credit - debit
        sheet.write(row, 0, when, day)
        sheet.write(row, 1, desc)
        if debit:
            sheet.write(row, 2, debit)
        if credit:
            sheet.write(row, 3, credit)
        sheet.write(row, 4, balance)
        row += 1
    sheet.write(row, 1, "Cộng phát sinh")
    sheet.write(row, 2, sum(e[2] for e in EVENTS))
    sheet.write(row, 3, sum(e[3] for e in EVENTS))
    sheet.write(row + 1, 1, "Số dư cuối kỳ")
    sheet.write(row + 1, 4, balance)
    book.save(str(OUT))


if __name__ == "__main__":
    legacy()
