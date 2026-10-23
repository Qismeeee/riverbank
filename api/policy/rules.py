from __future__ import annotations

from .engine import PolicyConfig, PolicyEngine, PolicyInput, Rule, RuleResult


def _result(rule_id: str, hard: bool, passed: bool, reason: str) -> RuleResult:
    return RuleResult(rule_id, hard, passed, reason)


def _missing(rule_id: str, hard: bool, what: str) -> RuleResult:
    return RuleResult(rule_id, hard, False, f"thiếu dữ liệu: {what}", missing_data=True)


def r01_max_amount(p: PolicyInput, c: PolicyConfig) -> RuleResult:
    return _result(
        "R01",
        True,
        p.amount <= c.max_amount,
        f"số tiền {p.amount:,} so với hạn mức {c.max_amount:,}",
    )


def r02_shop_age(p: PolicyInput, c: PolicyConfig) -> RuleResult:
    if p.shop_age_months is None:
        return _missing("R02", True, "tuổi shop")
    ok = p.shop_age_months >= c.min_shop_age_months
    return _result(
        "R02", True, ok, f"tuổi shop {p.shop_age_months} tháng, cần {c.min_shop_age_months}"
    )


def r03_return_rate(p: PolicyInput, c: PolicyConfig) -> RuleResult:
    if p.return_rate is None:
        return _missing("R03", False, "tỷ lệ hoàn")
    ok = p.return_rate <= c.max_return_rate
    return _result(
        "R03", False, ok, f"tỷ lệ hoàn {p.return_rate:.1%}, trần {c.max_return_rate:.0%}"
    )


def r04_tenor(p: PolicyInput, c: PolicyConfig) -> RuleResult:
    ok = c.min_tenor_weeks <= p.tenor_weeks <= c.max_tenor_weeks
    return _result(
        "R04",
        True,
        ok,
        f"kỳ hạn {p.tenor_weeks} tuần, khung {c.min_tenor_weeks} đến {c.max_tenor_weeks}",
    )


def r05_docs_when_over_platform(p: PolicyInput, c: PolicyConfig) -> RuleResult:
    threshold = int(p.platform_supported_amount * c.doc_required_multiple)
    requested = p.requested_amount if p.requested_amount is not None else p.amount
    if requested <= threshold:
        return _result(
            "R05", True, True, f"{requested:,} trong mức sàn hỗ trợ x{c.doc_required_multiple}"
        )
    if p.docs_verified is None:
        return _missing("R05", True, "kết quả Document Verifier")
    state = "đã" if p.docs_verified else "chưa"
    return _result("R05", True, p.docs_verified, f"vượt {threshold:,}, chứng từ {state} xác minh")


def r06_cic_group(p: PolicyInput, c: PolicyConfig) -> RuleResult:
    if p.cic_group is None:
        return _missing("R06", True, "nhóm nợ CIC")
    ok = p.cic_group <= c.max_cic_group
    return _result("R06", True, ok, f"nhóm nợ {p.cic_group}, chấp nhận đến nhóm {c.max_cic_group}")


def r07_outstanding(p: PolicyInput, c: PolicyConfig) -> RuleResult:
    if p.cic_outstanding is None:
        return _missing("R07", False, "dư nợ CIC")
    monthly = p.payout_stress * 4
    ok = p.cic_outstanding <= monthly * c.max_outstanding_to_monthly_payout
    return _result("R07", False, ok, f"dư nợ {p.cic_outstanding:,} so với payout tháng {monthly:,}")


def r08_deduction_cap(p: PolicyInput, c: PolicyConfig) -> RuleResult:
    cap = int(p.payout_stress * c.deduction_cap_of_stress_payout)
    return _result(
        "R08",
        True,
        p.weekly_deduction <= cap,
        f"kỳ trích {p.weekly_deduction:,} so với trần {cap:,}",
    )


def r09_e_lending_cap(p: PolicyInput, c: PolicyConfig) -> RuleResult:
    if not p.borrower_is_individual:
        return _result("R09", True, True, "không áp dụng cho hộ kinh doanh, pháp chế xác nhận")
    ok = p.amount <= c.e_lending_cap
    return _result(
        "R09", True, ok, f"vay điện tử {p.amount:,}, trần {c.e_lending_cap:,} (TT 06/2023)"
    )


RULES: list[Rule] = [
    Rule("R09", True, r09_e_lending_cap),
    Rule("R01", True, r01_max_amount),
    Rule("R04", True, r04_tenor),
    Rule("R08", True, r08_deduction_cap),
    Rule("R02", True, r02_shop_age),
    Rule("R03", False, r03_return_rate),
    Rule("R05", True, r05_docs_when_over_platform),
    Rule("R06", True, r06_cic_group),
    Rule("R07", False, r07_outstanding),
]


def default_engine(config: PolicyConfig | None = None) -> PolicyEngine:
    return PolicyEngine(RULES, config)
