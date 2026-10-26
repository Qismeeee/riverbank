from .models import PLAN_FIELDS, FieldIssue, PlanDraft

FIELD_LABELS = {
    "lot_value": "giá trị lô hàng",
    "cash_on_hand": "số vốn bạn đang có",
    "supplier_schedule": "lịch trả tiền cho xưởng theo tuần",
    "sell_weeks": "số tuần bán hết lô",
    "requested_amount": "số tiền muốn vay",
}
PLAN_LABEL = "kế hoạch"
READY = "Đủ thông tin rồi, bạn bấm Gửi để hệ thống tính."
CHECK = "Bạn kiểm tra lại {fields} giúp mình nhé."


def vnd(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + " đ"


class PlanReply:
    def compose(self, draft: PlanDraft, question: str | None, issues: list[FieldIssue]) -> str:
        parts = []
        if noted := self._noted(draft):
            parts.append(f"Mình đã ghi {noted}.")
        if question:
            parts.append(question)
        elif issues:
            fields = dict.fromkeys(FIELD_LABELS.get(i.field, PLAN_LABEL) for i in issues)
            parts.append(CHECK.format(fields=", ".join(fields)))
        else:
            parts.append(READY)
        return " ".join(parts)

    def _noted(self, draft: PlanDraft) -> str:
        return ", ".join(
            f"{FIELD_LABELS[name]} {self._value(name, value)}"
            for name in PLAN_FIELDS
            if (value := getattr(draft, name)) is not None
        )

    @staticmethod
    def _value(name: str, value) -> str:
        if name == "supplier_schedule":
            return ", ".join(f"tuần {p.week} trả {vnd(p.amount)}" for p in value)
        if name == "sell_weeks":
            return f"{value} tuần"
        return vnd(value)
