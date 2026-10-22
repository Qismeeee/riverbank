from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal

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
    stress_weeks: list[dict] = field(default_factory=list)
    weekly_deductions: list[int] = field(default_factory=list)

    @property
    def loan_needed(self) -> bool:
        return self.gap > 0


def round_up_to_unit(x: int | float | Decimal, unit: int = ROUND_UNIT) -> int:
    return int((Decimal(str(x)) / unit).to_integral_value(rounding=ROUND_CEILING)) * unit


@dataclass(frozen=True)
class LoanTerms:
    amount: int
    tenor_weeks: int
    weekly_deductions: list[int]

    @property
    def weekly_deduction(self) -> int:
        return max(self.weekly_deductions)


class CashFlowEngine:
    def __init__(self, config: CashFlowConfig | None = None):
        self.cfg = config or CashFlowConfig()

    def compute(self, inp: CashFlowInput) -> CashFlowResult:
        if inp.cash_on_hand < 0 or inp.sell_weeks < 1:
            raise ValueError("tiền mặt và kỳ bán không hợp lệ")
        if any(p < 0 for p in inp.payout_weekly_history):
            raise ValueError("payout không được âm")
        if any(s["week"] < 0 or s["amount"] <= 0 for s in inp.supplier_schedule):
            raise ValueError("lịch trả nhà cung cấp không hợp lệ")
        if len(inp.payout_weekly_history) < self.cfg.min_history_weeks:
            raise ValueError(f"cần tối thiểu {self.cfg.min_history_weeks} tuần payout")
        history = sorted(inp.payout_weekly_history)
        center = len(history) // 2
        payout_base = (history[(len(history) - 1) // 2] + history[center]) // 2
        payout_stress = int(Decimal(payout_base) * Decimal(str(self.cfg.stress_factor)))
        rows = self._balances(inp, payout_base)
        gap, negative = self._gap(rows)
        result = CashFlowResult(payout_base, payout_stress, rows, gap, negative)
        result.stress_weeks = self._balances(inp, payout_stress)
        if gap == 0:
            result.payout_warning_threshold = self.warning_threshold(inp)
            return result
        self._propose(result)
        return result

    def warning_threshold(self, inp: CashFlowInput) -> int:
        total = 0
        threshold = 0
        for row in self._balances(inp, 0):
            total += row["outflow"]
            need = max(0, total - inp.cash_on_hand)
            periods = max(0, row["week"] - self.cfg.payout_start_week + 1)
            if need and periods == 0:
                raise ValueError("thiếu vốn trước kỳ payout đầu tiên")
            if periods:
                threshold = max(threshold, (need + periods - 1) // periods)
        return threshold

    def counter_terms(self, amount: int, tenor_weeks: int, payout_stress: int) -> LoanTerms:
        if amount <= 0 or not 2 <= tenor_weeks <= self.cfg.tenor_max_weeks:
            raise ValueError("số tiền hoặc kỳ hạn counter-offer không hợp lệ")
        cap = int(Decimal(payout_stress) * Decimal(str(self.cfg.deduction_cap_of_stress_payout)))
        while (amount + tenor_weeks - 1) // tenor_weeks > cap:
            if tenor_weeks >= self.cfg.tenor_max_weeks:
                raise ValueError("kỳ trích vượt trần payout xấu")
            tenor_weeks = min(tenor_weeks + self.cfg.tenor_extend_step, self.cfg.tenor_max_weeks)
        return LoanTerms(amount, tenor_weeks, self.schedule(amount, tenor_weeks))

    @staticmethod
    def schedule(amount: int, tenor: int) -> list[int]:
        regular, remainder = divmod(amount, tenor)
        return [regular + (i < remainder) for i in range(tenor)]

    def _propose(self, r: CashFlowResult) -> None:
        cfg = self.cfg
        amount = min(
            round_up_to_unit(Decimal(r.gap) * Decimal(str(cfg.buffer_factor))),
            int(Decimal(r.gap) * Decimal(str(cfg.buffer_cap))),
        )
        tenor = (r.negative_weeks[-1] - r.negative_weeks[0] + 1) + cfg.tenor_buffer_weeks
        tenor = min(tenor, cfg.tenor_max_weeks)
        cap = int(Decimal(str(cfg.deduction_cap_of_stress_payout)) * r.payout_stress)
        while True:
            deduction = (amount + tenor - 1) // tenor
            r.tenor_trace.append({"tenor": tenor, "deduction": deduction, "cap": cap})
            if deduction <= cap or tenor >= cfg.tenor_max_weeks:
                break
            tenor = min(tenor + cfg.tenor_extend_step, cfg.tenor_max_weeks)
        r.amount_proposed, r.tenor_weeks, r.weekly_deduction = amount, tenor, deduction
        r.weekly_deductions = self.schedule(amount, tenor)

    def _balances(self, inp: CashFlowInput, payout: int) -> list[dict]:
        outflow: dict[int, int] = {}
        for payment in inp.supplier_schedule:
            week = int(payment["week"])
            outflow[week] = outflow.get(week, 0) + int(payment["amount"])
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
