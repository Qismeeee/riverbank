import re
from dataclasses import dataclass

from services.guard import expand_units
from services.underwriting.evidence import TEMPLATES

from .replies import vnd

NO_TARIFF = "Chưa có biểu phí từ ngân hàng trong hồ sơ này."


@dataclass(frozen=True)
class Clause:
    text: str
    fields: frozenset[str]


def normalize(text: str) -> str:
    text = re.sub(
        r"\b\d+(?:[.,]\d{1,2})?\s*(?:triệu|tỷ)\b",
        lambda match: expand_units(match.group()) + " vnd",
        text.casefold(),
    )
    text = re.sub(r"(?<=\d)\.(?=\d{3}(?:\D|$))", "", text)
    text = re.sub(r"\b(?:vnd|đồng|đ)\b", "vnd", text)
    return " ".join(re.sub(r"[^\w/]+", " ", text).split())


def clauses(facts: dict, status_text: str, statement_step: str) -> list[Clause]:
    result = [
        Clause(status_text, frozenset({"status"})),
        Clause(NO_TARIFF, frozenset({"bank_tariff"})),
    ]
    amount = "final_amount" if facts.get("final_amount") else "amount_proposed"
    tenor = "final_tenor_weeks" if facts.get("final_tenor_weeks") else "tenor_weeks"
    if facts.get(amount) and facts.get(tenor) and facts.get("weekly_deduction"):
        terms = (
            f"{vnd(facts[amount])} trong {facts[tenor]} tuần, "
            f"mỗi tuần trừ {vnd(facts['weekly_deduction'])}"
        )
        fields = frozenset({amount, tenor, "weekly_deduction"})
        for prefix in ("Số tiền", "Hệ thống đề xuất", "Đề xuất"):
            for suffix in (".", " từ payout."):
                result.append(Clause(f"{prefix} {terms}{suffix}", fields))
    for template in ("gap", "proposal", "deduction", "no_loan"):
        wording, fields = TEMPLATES[template]
        if all(facts.get(name) is not None for name in fields):
            result.append(Clause(wording.format(**facts), frozenset(fields)))
    if window := facts.get("statement_window"):
        for text in (
            statement_step.format(**window),
            f"Bạn nộp sao kê trong khoảng {window['start']} đến {window['end']} nhé.",
        ):
            result.append(Clause(text, frozenset({"statement_window"})))
    if facts.get("los_reference"):
        result.append(
            Clause(
                f"Mã tham chiếu giải ngân {facts['los_reference']}.", frozenset({"los_reference"})
            )
        )
    for text in (
        "Ngân hàng sẽ xem hồ sơ; hệ thống không tự duyệt khoản vay.",
        "Tiền payout là tiền hàng Shopee trả về cho shop; một phần được trích để trả khoản vay.",
        "Nếu muốn đổi kế hoạch, bạn cần tạo hồ sơ mới.",
        "Copilot chỉ trả lời về hồ sơ và khoản vay này.",
    ):
        result.append(Clause(text, frozenset()))
    return result


def required_fields(question: str, facts: dict) -> set[str]:
    text = question.casefold()
    if any(term in text for term in ("lãi", "phí", "lãi suất")):
        return {"bank_tariff"}
    if facts.get("statement_window") and any(term in text for term in ("sao kê", "cần gì", "nộp")):
        return {"statement_window"}
    if any(term in text for term in ("mỗi tuần", "trích", "bị trừ", "payout")):
        return {"weekly_deduction"} if facts.get("weekly_deduction") else {"status"}
    if any(term in text for term in ("bao nhiêu", "bao lâu", "kỳ hạn")):
        return (
            {"final_amount" if facts.get("final_amount") else "amount_proposed"}
            if facts.get("amount_proposed")
            else {"status"}
        )
    return {"status"}


def semantic_errors(text: str, question: str, facts: dict, approved: list[Clause]) -> list[str]:
    remaining, used = normalize(text), set()
    choices = sorted(((normalize(c.text), c.fields) for c in approved), key=lambda c: -len(c[0]))
    while remaining:
        for wording, fields in choices:
            if remaining == wording or remaining.startswith(wording + " "):
                used.update(fields)
                remaining = remaining[len(wording) :].strip()
                break
        else:
            return ["semantic_mismatch"]
    required = required_fields(question, facts)
    return [] if required <= used else ["answer_incomplete"]
