from dataclasses import dataclass

from .documents import StatementLine, StatementSummary

BALANCE_TOLERANCE_VND = 2


@dataclass(frozen=True)
class TotalsCheck:
    opening: int
    closing: int
    expected_change: int
    read_change: int
    matched: bool

    def payload(self) -> dict:
        return {
            "opening": self.opening,
            "closing": self.closing,
            "expected_change": self.expected_change,
            "read_change": self.read_change,
            "matched": self.matched,
        }


@dataclass(frozen=True)
class BalanceCheck:
    checked_pairs: int
    mismatched_lines: tuple[int, ...]
    order: str | None
    totals: TotalsCheck | None = None

    @property
    def mismatches(self) -> int:
        return len(self.mismatched_lines)

    def payload(self) -> dict:
        return {
            "checked_pairs": self.checked_pairs,
            "mismatched_lines": list(self.mismatched_lines),
            "order": self.order,
            "totals": self.totals.payload() if self.totals else None,
        }


def _adjacent(prev: StatementLine, cur: StatementLine) -> bool:
    if prev.balance is None or cur.balance is None:
        return False
    if prev.page is None or cur.page is None:
        return prev.page == cur.page
    return cur.page - prev.page in (0, 1)


def _misses(lines: list[StatementLine], pairs: list[tuple[int, int]], tolerance: int) -> list[int]:
    return [
        cur
        for prev, cur in pairs
        if abs(
            lines[prev].balance
            + (lines[cur].credit or 0)
            - (lines[cur].debit or 0)
            - lines[cur].balance
        )
        > tolerance
    ]


def check_totals(
    lines: list[StatementLine], summary: StatementSummary, tolerance: int = BALANCE_TOLERANCE_VND
) -> TotalsCheck | None:
    if summary.opening is None or summary.closing is None:
        return None
    expected = summary.closing - summary.opening
    read = sum(line.credit or 0 for line in lines) - sum(line.debit or 0 for line in lines)
    return TotalsCheck(
        summary.opening, summary.closing, expected, read, abs(expected - read) <= tolerance
    )


def check_balances(
    lines: list[StatementLine],
    summary: StatementSummary | None = None,
    tolerance: int = BALANCE_TOLERANCE_VND,
) -> BalanceCheck:
    totals = check_totals(lines, summary, tolerance) if summary else None
    pairs = [(i - 1, i) for i in range(1, len(lines)) if _adjacent(lines[i - 1], lines[i])]
    if not pairs:
        return BalanceCheck(0, (), None, totals)
    oldest_first = _misses(lines, pairs, tolerance)
    newest_first = _misses(lines, [(cur, prev) for prev, cur in pairs], tolerance)
    if len(newest_first) < len(oldest_first):
        return BalanceCheck(len(pairs), tuple(sorted(newest_first)), "newest_first", totals)
    return BalanceCheck(len(pairs), tuple(oldest_first), "oldest_first", totals)
