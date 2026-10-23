from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from functools import cached_property

from services.features import payout_profile
from services.risk import RiskModel as PdScorer

PROFILE_FIELDS = ("archetype", "shop_age_months", "n_products", "top3_share", "rating")
SIMULATION_DISCLOSURE = "PD ước lượng trên dữ liệu mô phỏng"


@dataclass(frozen=True)
class RiskInput:
    features: dict
    plan: dict
    cashflow: dict


@dataclass(frozen=True)
class RiskFactor:
    name: str
    direction: str
    contribution: float
    value: float | None = None


@dataclass(frozen=True)
class RiskAssessment:
    model_version: str
    source: str
    pd_bps: int | None = None
    factors: list[RiskFactor] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    disclosure: str = SIMULATION_DISCLOSURE

    @property
    def available(self) -> bool:
        return not self.missing_fields

    def payload(self) -> dict:
        return asdict(self)


class RiskModel(ABC):
    @abstractmethod
    def assess(self, inp: RiskInput) -> RiskAssessment: ...

    def predict(self, inp: RiskInput) -> dict:
        return self.assess(inp).payload()


class DemoRiskModel(RiskModel):
    def assess(self, inp: RiskInput) -> RiskAssessment:
        return RiskAssessment(
            model_version="demo-fixed-v1",
            source="demo",
            pd_bps=500,
            factors=[RiskFactor("demo_baseline", "increase", 1.0)],
        )


class LightGbmRiskModel(RiskModel):
    def __init__(self, scorer: PdScorer | None = None, top_k: int = 5):
        self._scorer = scorer
        self.top_k = top_k

    @cached_property
    def scorer(self) -> PdScorer:
        return self._scorer or PdScorer.load()

    def assess(self, inp: RiskInput) -> RiskAssessment:
        version = self.scorer.meta.get("model_version", "unknown")
        raw = self.raw_features(inp)
        missing = sorted(name for name, value in raw.items() if value is None)
        if missing:
            return RiskAssessment(version, "lightgbm", missing_fields=missing)
        result = self.scorer.predict(raw, top_k=self.top_k)
        return RiskAssessment(
            model_version=result.model_version,
            source="lightgbm",
            pd_bps=round(result.pd * 10_000),
            factors=[
                RiskFactor(f.name, "increase" if f.shap > 0 else "decrease", f.shap, f.value)
                for f in result.factors
            ],
        )

    @staticmethod
    def raw_features(inp: RiskInput) -> dict:
        features, plan, cf = inp.features, inp.plan, inp.cashflow
        history = [week["amount"] for week in features["weekly_payouts"]]
        raw = {name: features.get(name) for name in PROFILE_FIELDS}
        raw.update(payout_profile(history))
        raw.update(
            return_rate_12w=features.get("return_rate"),
            cash_on_hand=plan["cash_on_hand"],
            supplier_amount=sum(p["amount"] for p in plan["supplier_schedule"]),
            amount=cf["amount_proposed"],
            tenor_weeks=cf["tenor_weeks"],
            deduction_to_stress=round(cf["weekly_deduction"] / max(1, cf["payout_stress"]), 4),
        )
        return raw
