import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

import jev
from services.metering import record_usage
from services.underwriting.models import WorkflowError

from .documents import fold

PAYOUT_CRITERIA = (
    "Có: tiền sàn Shopee trả shop, ví dụ payout, quyết toán doanh thu, IBFT gắn Shopee, Shopee VN",
    "Không: chuyển khoản cá nhân, vay mượn, thu ngoài sàn, hoàn tiền chiều khác, "
    "hoặc mô tả không đủ để gắn với sàn",
)


@dataclass(frozen=True)
class PayoutLabel:
    is_payout: bool
    probability: float | None = None


class PayoutClassifier(ABC):
    name: str
    model: str | None = None

    @abstractmethod
    def classify(self, descriptions: list[str]) -> list[PayoutLabel]: ...


class PatternPayoutClassifier(PayoutClassifier):
    name = "pattern"

    def __init__(self, pattern: str):
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.model = f"regex:{pattern}"

    def classify(self, descriptions: list[str]) -> list[PayoutLabel]:
        return [PayoutLabel(bool(self.pattern.search(fold(d)))) for d in descriptions]


class JevPayoutClassifier(PayoutClassifier):
    name = "jev"
    model = jev.MODEL

    def __init__(
        self,
        threshold: float = 0.5,
        batch_size: int = 20,
        timeout: float = 20.0,
        decide: Callable[..., dict] = jev.decide,
    ):
        self.threshold, self.batch_size = threshold, batch_size
        self.timeout, self.decide = timeout, decide

    def classify(self, descriptions: list[str]) -> list[PayoutLabel]:
        labels: list[PayoutLabel] = []
        for start in range(0, len(descriptions), self.batch_size):
            batch = descriptions[start : start + self.batch_size]
            questions = {
                f"line_{i}": jev.noul(
                    f'Dòng tiền vào trên sao kê có mô tả "{text}". '
                    "Đây có phải tiền sàn Shopee trả cho shop không?",
                    *PAYOUT_CRITERIA,
                )
                for i, text in enumerate(batch)
            }
            try:
                jev.forget_last()
                answers = self.decide(
                    ["Sao kê ngân hàng của một shop bán trên Shopee."],
                    questions,
                    timeout=self.timeout,
                )
                probabilities = [jev.noul_probability(answers, name) for name in questions]
            except Exception as exc:
                raise WorkflowError(
                    "STATEMENT_CLASSIFIER_UNAVAILABLE", 503, [type(exc).__name__]
                ) from exc
            model, usage = jev.take_last()
            record_usage("statement_classifier", "openrouter", model or self.model, usage, False)
            labels.extend(PayoutLabel(p >= self.threshold, p) for p in probabilities)
        return labels


class GatedPayoutClassifier(PayoutClassifier):
    def __init__(self, gate: PayoutClassifier, judge: PayoutClassifier):
        self.gate, self.judge = gate, judge
        self.name = f"{gate.name}+{judge.name}"
        self.model = judge.model

    def classify(self, descriptions: list[str]) -> list[PayoutLabel]:
        labels = self.gate.classify(descriptions)
        candidates = [i for i, label in enumerate(labels) if label.is_payout]
        judged = self.judge.classify([descriptions[i] for i in candidates])
        verdicts = dict(zip(candidates, judged, strict=True))
        return [verdicts.get(i, PayoutLabel(False)) for i in range(len(descriptions))]
