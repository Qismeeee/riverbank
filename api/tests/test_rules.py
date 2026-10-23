from policy import PolicyConfig, PolicyEngine, PolicyInput, Rule, RuleResult, default_engine

ENGINE = default_engine()


def _case_b(**kw) -> PolicyInput:
    base = dict(
        amount=90_000_000,
        tenor_weeks=8,
        weekly_deduction=11_250_000,
        payout_stress=49_000_000,
        platform_supported_amount=60_000_000,
        shop_age_months=14,
        return_rate=0.05,
        cic_group=1,
        cic_outstanding=0,
    )
    base.update(kw)
    return PolicyInput(**base)


def test_case_b_passes_all_hard_rules():
    out = ENGINE.evaluate(_case_b())
    assert not out.hard_fail
    assert out.failed_rule_ids == []


def test_r08_fails_when_deduction_over_cap():
    out = ENGINE.evaluate(_case_b(weekly_deduction=22_500_000))
    assert out.hard_fail and "R08" in out.failed_rule_ids


def test_r09_e_lending_cap_is_hard():
    out = ENGINE.evaluate(_case_b(amount=120_000_000))
    assert "R09" in out.failed_rule_ids and out.hard_fail


def test_r09_not_applied_for_business_household():
    out = ENGINE.evaluate(_case_b(amount=120_000_000, borrower_is_individual=False))
    assert "R09" not in out.failed_rule_ids


def test_r05_requires_docs_when_over_platform_multiple():
    out = ENGINE.evaluate(_case_b(platform_supported_amount=30_000_000))
    r05 = next(r for r in out.results if r.rule_id == "R05")
    assert r05.missing_data and out.hard_fail
    out2 = ENGINE.evaluate(_case_b(platform_supported_amount=30_000_000, docs_verified=True))
    assert "R05" not in out2.failed_rule_ids


def test_missing_cic_escalates():
    out = ENGINE.evaluate(_case_b(cic_group=None))
    assert out.hard_fail and "R06" in out.failed_rule_ids


def test_soft_rule_only_warns():
    out = ENGINE.evaluate(_case_b(return_rate=0.2))
    assert not out.hard_fail
    assert [r.rule_id for r in out.soft_warnings] == ["R03"]


def test_config_is_overridable():
    out = default_engine(PolicyConfig(max_amount=50_000_000)).evaluate(_case_b())
    assert "R01" in out.failed_rule_ids


def test_custom_rule_set_is_pluggable():
    always_fail = Rule("R99", True, lambda p, c: RuleResult("R99", True, False, "test"))
    out = PolicyEngine([always_fail]).evaluate(_case_b(evidence_ids={"R99": "E9"}))
    assert out.hard_fail and out.results[0].evidence_id == "E9"


def test_r05_uses_requested_amount_when_provided():
    from policy.engine import PolicyConfig, PolicyInput
    from policy.rules import r05_docs_when_over_platform

    inp = PolicyInput(
        amount=90_000_000,
        tenor_weeks=8,
        weekly_deduction=11_250_000,
        payout_stress=49_000_000,
        platform_supported_amount=40_000_000,
        requested_amount=150_000_000,
    )
    result = r05_docs_when_over_platform(inp, PolicyConfig())
    assert not result.passed and result.missing_data
