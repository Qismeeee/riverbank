from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class PolicyConfig:
    max_amount: int = 300_000_000
    min_shop_age_months: int = 6
    max_return_rate: float = 0.15
    min_tenor_weeks: int = 2
    max_tenor_weeks: int = 26
    doc_required_multiple: float = 2.5
    max_cic_group: int = 2
    max_outstanding_to_monthly_payout: float = 3.0
    deduction_cap_of_stress_payout: float = 0.25
    e_lending_cap: int = 100_000_000


@dataclass(frozen=True)
class PolicyInput:
    amount: int
    tenor_weeks: int
    weekly_deduction: int
    payout_stress: int
    platform_supported_amount: int
    shop_age_months: int | None = None
    return_rate: float | None = None
    docs_verified: bool | None = None
    cic_group: int | None = None
    cic_outstanding: int | None = None
    borrower_is_individual: bool = True
    evidence_ids: dict[str, str] = field(default_factory=dict)
    requested_amount: int | None = None


@dataclass
class RuleResult:
    rule_id: str
    hard: bool
    passed: bool
    reason: str
    missing_data: bool = False
    evidence_id: str | None = None


@dataclass
class PolicyOutcome:
    results: list[RuleResult]

    @property
    def hard_fail(self) -> bool:
        return any(r.hard and not r.passed for r in self.results)

    @property
    def soft_warnings(self) -> list[RuleResult]:
        return [r for r in self.results if not r.hard and not r.passed]

    @property
    def failed_rule_ids(self) -> list[str]:
        return [r.rule_id for r in self.results if not r.passed]


RuleFn = Callable[[PolicyInput, PolicyConfig], RuleResult]


@dataclass(frozen=True)
class Rule:
    rule_id: str
    hard: bool
    check: RuleFn


class PolicyEngine:
    def __init__(self, rules: list[Rule], config: PolicyConfig | None = None):
        self.rules = rules
        self.cfg = config or PolicyConfig()

    def evaluate(self, inp: PolicyInput) -> PolicyOutcome:
        results = []
        for rule in self.rules:
            result = rule.check(inp, self.cfg)
            result.evidence_id = inp.evidence_ids.get(rule.rule_id)
            results.append(result)
        return PolicyOutcome(results)
