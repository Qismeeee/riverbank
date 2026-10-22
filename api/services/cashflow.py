from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

ROUND_UNIT = 5_000_000


@dataclass(frozen=True)
class CashFlowConfig:
    stress_factor: float = 0.7
    buffer_factor: float = 1.1
    buffer_cap: float = 1.15
    tenor_buffer_weeks: int = 2
    deduction_cap_of_stress_payout: float = 0.25
    tenor_extend_step: int = 2
    tenor_max_weeks: int = 26
    payout_start_week: int = 1
    extra_weeks: int = 2
    min_history_weeks: int = 4


@dataclass(frozen=True)
class CashFlowInput:
    cash_on_hand: int
    supplier_schedule: list[dict]
    sell_weeks: int
    payout_weekly_history: list[int]
    requested_amount: int | None = None


@dataclass
class CashFlowResult:
    payout_base: int
    payout_stress: int
    weeks: list[dict] = field(default_factory=list)
    gap: int = 0
    negative_weeks: list[int] = field(default_factory=list)
    amount_proposed: int = 0
    tenor_weeks: int = 0
    weekly_deduction: int = 0
    tenor_trace: list[dict] = field(default_factory=list)
    payout_warning_threshold: int | None = None

    @property
    def loan_needed(self) -> bool:
        return self.gap > 0


def round_up_to_unit(x: float, unit: int = ROUND_UNIT) -> int:
    return int(-(-x // unit) * unit)


class CashFlowEngine:
    def __init__(self, config: CashFlowConfig | None = None):
        self.cfg = config or CashFlowConfig()

    def compute(self, inp: CashFlowInput) -> CashFlowResult:
        if len(inp.payout_weekly_history) < self.cfg.min_history_weeks:
            raise ValueError(f"cần tối thiểu {self.cfg.min_history_weeks} tuần payout")
        payout_base = int(median(inp.payout_weekly_history))
        payout_stress = int(payout_base * self.cfg.stress_factor)
        rows = self._balances(inp, payout_stress)
        gap, negative = self._gap(rows)
        result = CashFlowResult(payout_base, payout_stress, rows, gap, negative)
        if gap == 0:
            result.payout_warning_threshold = self.warning_threshold(inp)
            return result
        self._propose(result)
        return result

    def warning_threshold(self, inp: CashFlowInput) -> int:
        lo = 0
        hi = int(median(inp.payout_weekly_history) * self.cfg.stress_factor)
        if self._gap(self._balances(inp, lo))[0] == 0:
            return 0
        while hi - lo > 1_000_000:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if self._gap(self._balances(inp, mid))[0] > 0 else (lo, mid)
        return round_up_to_unit(lo)

    def _propose(self, r: CashFlowResult) -> None:
        cfg = self.cfg
        amount = min(round_up_to_unit(r.gap * cfg.buffer_factor), int(r.gap * cfg.buffer_cap))
        tenor = (r.negative_weeks[-1] - r.negative_weeks[0] + 1) + cfg.tenor_buffer_weeks
        cap = int(cfg.deduction_cap_of_stress_payout * r.payout_stress)
        while True:
            deduction = amount // tenor
            r.tenor_trace.append({"tenor": tenor, "deduction": deduction, "cap": cap})
            if deduction <= cap or tenor >= cfg.tenor_max_weeks:
                break
            tenor += cfg.tenor_extend_step
        r.amount_proposed, r.tenor_weeks, r.weekly_deduction = amount, tenor, deduction

    def _balances(self, inp: CashFlowInput, payout: int) -> list[dict]:
        outflow = {int(s["week"]): int(s["amount"]) for s in inp.supplier_schedule}
        horizon = max(inp.sell_weeks, max(outflow, default=0) + 1) + self.cfg.extra_weeks
        rows, balance = [], inp.cash_on_hand
        for week in range(horizon):
            inflow = payout if week >= self.cfg.payout_start_week else 0
            start = balance
            balance = start + inflow - outflow.get(week, 0)
            rows.append(
                {
                    "week": week,
                    "bal_start": start,
                    "payout": inflow,
                    "outflow": outflow.get(week, 0),
                    "bal_end": balance,
                }
            )
        return rows

    @staticmethod
    def _gap(rows: list[dict]) -> tuple[int, list[int]]:
        negative = [r["week"] for r in rows if r["bal_end"] < 0]
        gap = abs(min(r["bal_end"] for r in rows)) if negative else 0
        return gap, negative
