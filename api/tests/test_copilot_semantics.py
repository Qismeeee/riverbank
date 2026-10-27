import pytest

from services.copilot.models import AskInput
from tests.test_copilot import advisor, answer, case_b  # noqa: F401


@pytest.mark.parametrize(
    "text,fields",
    [
        ("Seller muốn vay 90000000 VND trong 8 tuần.", ["amount_proposed", "tenor_weeks"]),
        ("Đề xuất 8 VND trong 90000000 tuần.", ["amount_proposed", "tenor_weeks"]),
        ("Phí hồ sơ là năm triệu đồng.", []),
        ("Chào bạn, hôm nay trời đẹp.", []),
        ("Ngân hàng đã chấp nhận khoản vay.", []),
    ],
)
def test_adversarial_claims_cannot_pass_as_llm_answers(case_b, text, fields):  # noqa: F811
    _, case = case_b
    result = advisor(answer(text, *fields)).ask(
        case, AskInput(question="Tôi được đề xuất vay bao nhiêu?")
    )
    assert result.source == "template"
    assert result.guard_codes
    assert "90.000.000" in result.answer and "8 tuần" in result.answer


def test_fee_fallback_explains_missing_tariff(case_b):  # noqa: F811
    _, case = case_b
    result = advisor(answer("Phí hồ sơ là năm triệu đồng.")).ask(
        case, AskInput(question="Lãi suất và phí là bao nhiêu?")
    )
    assert result.source == "template"
    assert result.answer == "Chưa có biểu phí từ ngân hàng trong hồ sơ này."
