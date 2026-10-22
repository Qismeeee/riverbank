from services.cashflow import CashFlowEngine, CashFlowInput, round_up_to_unit

ENGINE = CashFlowEngine()


def _case_b() -> CashFlowInput:
    return CashFlowInput(
        cash_on_hand=120_000_000,
        supplier_schedule=[{"week": 0, "amount": 200_000_000}],
        sell_weeks=6,
        payout_weekly_history=[70_000_000] * 12,
        requested_amount=150_000_000,
    )


def test_round_up_to_5m():
    assert round_up_to_unit(88_000_000) == 90_000_000
    assert round_up_to_unit(90_000_000) == 90_000_000
    assert round_up_to_unit(1) == 5_000_000


def test_case_b_numbers_match_docs():
    r = ENGINE.compute(_case_b())
    assert r.payout_stress == 49_000_000
    assert r.gap == 80_000_000
    assert r.negative_weeks == [0, 1]
    assert r.amount_proposed == 90_000_000
    assert r.tenor_weeks == 8
    assert r.weekly_deduction == 11_250_000
    assert [t["tenor"] for t in r.tenor_trace] == [4, 6, 8]


def test_proposed_never_exceeds_gap_cap():
    r = ENGINE.compute(_case_b())
    assert r.amount_proposed <= r.gap * 1.15
    assert r.amount_proposed < 150_000_000


def test_gap_zero_has_warning_threshold():
    inp = CashFlowInput(
        cash_on_hand=150_000_000,
        supplier_schedule=[{"week": 0, "amount": 120_000_000}],
        sell_weeks=6,
        payout_weekly_history=[40_000_000] * 12,
    )
    r = ENGINE.compute(inp)
    assert r.gap == 0 and not r.loan_needed
    assert r.amount_proposed == 0
    assert r.payout_warning_threshold is not None
