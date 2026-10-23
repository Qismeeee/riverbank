from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass

import jev

CIC_QUESTION = jev.noul(
    "Hồ sơ vay vốn lưu động này đã đủ điều kiện để tra cứu CIC tốn phí chưa?",
    "Đủ điều kiện: cần vay thật, dòng tiền và hồ sơ shop không có dấu hiệu loại trừ",
    "Chưa đủ điều kiện hoặc có dấu hiệu loại trừ rõ ràng",
)


@dataclass(frozen=True)
class GateInput:
    features: dict
    cashflow: dict
    risk: dict


@dataclass(frozen=True)
class GateDecision:
    gate: str
    eligible: bool
    threshold: float
    model: str
    probability: float | None = None
    error: str | None = None

    def payload(self) -> dict:
        return asdict(self)


class CicGate(ABC):
    name = "cic"
    model = "open"
    threshold = 0.0

    @abstractmethod
    def evaluate(self, inp: GateInput) -> GateDecision: ...

    def describe(self) -> dict:
        return {"gate": self.name, "model": self.model, "threshold": self.threshold}


class OpenCicGate(CicGate):
    def evaluate(self, inp: GateInput) -> GateDecision:
        return GateDecision(self.name, True, self.threshold, self.model)


class JevCicGate(CicGate):
    model = jev.MODEL

    def __init__(
        self,
        threshold: float = 0.6,
        timeout: float = 10.0,
        decide: Callable[..., dict] = jev.decide,
    ):
        self.threshold, self.timeout, self.decide = threshold, timeout, decide

    def evaluate(self, inp: GateInput) -> GateDecision:
        try:
            answers = self.decide(state(inp), {"eligible": CIC_QUESTION}, timeout=self.timeout)
            probability = jev.noul_probability(answers, "eligible")
        except Exception as exc:
            return GateDecision(
                self.name, False, self.threshold, self.model, error=type(exc).__name__
            )
        return GateDecision(
            self.name, probability >= self.threshold, self.threshold, self.model, probability
        )


def _known(value, unit: str = "") -> str:
    return "chưa rõ" if value is None else f"{value}{unit}"


def state(inp: GateInput) -> list[str]:
    f, cf, risk = inp.features, inp.cashflow, inp.risk
    return [
        f"Gap vốn {cf['gap']} VND. Đề xuất {cf['amount_proposed']} VND trong "
        f"{cf['tenor_weeks']} tuần, trích tối đa {max(cf['weekly_deductions'], default=0)} "
        "VND mỗi tuần.",
        f"Payout cơ sở {cf['payout_base']} VND mỗi tuần, kịch bản xấu {cf['payout_stress']} "
        "VND mỗi tuần.",
        f"Tuổi shop {_known(f.get('shop_age_months'), ' tháng')}, tỷ lệ hoàn hàng "
        f"{_known(f.get('return_rate'))}, đánh giá {_known(f.get('rating'))}.",
        f"PD ước lượng {_known(risk.get('pd_bps'), ' điểm cơ bản')}.",
    ]
