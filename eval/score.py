from __future__ import annotations

import re
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARAMETERS = ROOT / "policy" / "parameters.yaml"
GUARDRAILS = ROOT / "knowledge" / "guardrails.md"

INJECTION_MARKERS = (
    "ép duyệt",
    "bỏ qua chính sách",
    "bỏ qua luật",
    "ignore previous",
    "tiết lộ prompt",
    "đặt trạng thái accepted",
    "đặt accepted",
)
SENSITIVE_MARKERS = (
    "giới tính",
    "tôn giáo",
    "dân tộc",
    "sức khỏe",
    "gender",
    "religion",
    "ethnicity",
)


def _coerce(raw: str) -> int | str:
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def _parse_parameters(text: str) -> dict:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, val = line.strip().partition(":")
        val = val.strip().strip('"').strip("'")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if val == "":
            node: dict = {}
            parent[key] = node
            stack.append((indent, node))
        else:
            parent[key] = _coerce(val)
    return root


@lru_cache(maxsize=1)
def parameters() -> dict:
    return _parse_parameters(PARAMETERS.read_text(encoding="utf-8"))


def _dec(value: int | str) -> Decimal:
    return Decimal(str(value))


def _trunc_mul(amount: int, factor: int | str) -> int:
    product = _dec(amount) * _dec(factor)
    return int(product.to_integral_value(rounding=ROUND_DOWN))


def _round_nearest(amount: Decimal, unit: int) -> int:
    units = (amount / Decimal(unit)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(units) * unit


def _ceil_div(amount: int, parts: int) -> int:
    return (amount + parts - 1) // parts


def _exceeds(value: Decimal, threshold: Decimal, compare: str) -> bool:
    if compare == "greater_or_equal":
        return value >= threshold
    if compare == "greater_than":
        return value > threshold
    raise ValueError(f"phép so sánh chưa hỗ trợ: {compare}")


def _median(history: list[int], window: int) -> int:
    sample = sorted(history[-window:])
    if not sample:
        raise ValueError("thiếu lịch sử payout")
    mid = len(sample) // 2
    if len(sample) % 2:
        return sample[mid]
    return (sample[mid - 1] + sample[mid]) // 2


def _outflows(schedule: list[dict]) -> dict[int, int]:
    outflow: dict[int, int] = {}
    for payment in schedule:
        week = int(payment["week"])
        outflow[week] = outflow.get(week, 0) + int(payment["amount"])
    return outflow


def _rows(cash: int, outflow: dict[int, int], horizon: int, payout: int, week0: int) -> list[dict]:
    balance = cash
    rows = []
    for week in range(horizon):
        inflow = week0 if week == 0 else payout
        spent = outflow.get(week, 0)
        end = balance + inflow - spent
        rows.append(
            {"week": week, "start": balance, "inflow": inflow, "outflow": spent, "end": end}
        )
        balance = end
    return rows


def _warning_threshold(cash: int, outflow: dict[int, int], horizon: int) -> int:
    total = 0
    threshold = 0
    for week in range(horizon):
        total += outflow.get(week, 0)
        need = max(0, total - cash)
        periods = week
        if need and periods == 0:
            raise ValueError("thiếu vốn trước kỳ payout đầu tiên")
        if need and periods:
            threshold = max(threshold, _ceil_div(need, periods))
    return threshold


def _cashflow(inp: dict, cfg: dict) -> dict:
    cash_cfg = cfg["cashflow"]
    if cash_cfg["payout_statistic"] != "median":
        raise ValueError("eval chỉ chấm payout_statistic = median")
    if cash_cfg["stress_rounding"] != "trunc_toward_zero":
        raise ValueError("eval chỉ chấm stress_rounding = trunc_toward_zero")
    if cash_cfg["weekly_deduction_rounding"] != "ceil_vnd":
        raise ValueError("eval chỉ chấm weekly_deduction_rounding = ceil_vnd")
    if int(cash_cfg["week0_inflow"]) != 0:
        raise ValueError("tuần 0 của lô không có inflow")

    history = [int(v) for v in inp["payout_weekly_history"]]
    payout_base = _median(history, int(cash_cfg["payout_window_weeks"]))
    payout_stress = _trunc_mul(payout_base, cash_cfg["stress_factor"])
    outflow = _outflows(inp["supplier_schedule"])
    horizon = max(int(inp["sell_weeks"]), max(outflow, default=0)) + 1
    rows = _rows(int(inp["cash_on_hand"]), outflow, horizon, payout_base, 0)
    negative = [row["week"] for row in rows if row["end"] < 0]
    gap = abs(min(row["end"] for row in rows)) if negative else 0
    result = {
        "payout_base": payout_base,
        "payout_stress": payout_stress,
        "gap": gap,
        "negative_weeks": negative,
        "balances": rows,
        "amount_proposed": 0,
        "tenor_weeks": 0,
        "weekly_deduction": 0,
        "tenor_trace": [],
        "deduction_cap": None,
        "payout_warning_threshold": None,
        "warning_at_threshold_min_end": None,
        "warning_below_has_gap": None,
    }
    if gap == 0:
        threshold = _warning_threshold(int(inp["cash_on_hand"]), outflow, horizon)
        at_rows = _rows(int(inp["cash_on_hand"]), outflow, horizon, threshold, 0)
        result["payout_warning_threshold"] = threshold
        result["warning_at_threshold_min_end"] = min(row["end"] for row in at_rows)
        if threshold > 0:
            below = _rows(int(inp["cash_on_hand"]), outflow, horizon, threshold - 1, 0)
            result["warning_below_has_gap"] = min(row["end"] for row in below) < 0
        return result

    raw = min(
        _dec(gap) * _dec(cash_cfg["buffer_factor"]),
        _dec(gap) * _dec(cash_cfg["buffer_cap"]),
    )
    proposed = _round_nearest(raw, int(cash_cfg["amount_rounding_vnd"]))
    span = negative[-1] - negative[0] + 1
    tenor = min(span + int(cash_cfg["tenor_buffer_weeks"]), int(cash_cfg["max_tenor_weeks"]))
    cap = _trunc_mul(payout_stress, cash_cfg["deduction_cap_ratio"])
    step = int(cash_cfg["tenor_extend_step_weeks"])
    trace: list[dict] = []
    while True:
        deduction = _ceil_div(proposed, tenor)
        trace.append({"tenor_weeks": tenor, "weekly_deduction": deduction})
        if deduction <= cap or tenor >= int(cash_cfg["max_tenor_weeks"]):
            break
        tenor = min(tenor + step, int(cash_cfg["max_tenor_weeks"]))
    result["amount_proposed"] = proposed
    result["tenor_weeks"] = tenor
    result["weekly_deduction"] = deduction
    result["tenor_trace"] = trace
    result["deduction_cap"] = cap
    return result


def _rule(rule_id: str, hard: bool, passed: bool, effect: str, reason: str) -> dict:
    return {
        "rule_id": rule_id,
        "hard": hard,
        "passed": passed,
        "effect": effect,
        "reason_code": reason if not passed else "NONE",
    }


def _evaluate(cf: dict, inp: dict, cfg: dict, statement: dict | None) -> dict:
    routing = cfg["routing"]
    appetite = cfg["appetite"]
    proposed = int(cf["amount_proposed"])
    tenor = int(cf["tenor_weeks"])
    deduction = int(cf["weekly_deduction"])
    limit = int(inp["platform_support_limit"])
    requested = int(inp.get("requested_amount") or 0)
    rules: list[dict] = []

    rules.append(
        _rule(
            "R01",
            True,
            proposed <= limit,
            "NONE" if proposed <= limit else "ESCALATE",
            "R01_ABOVE_PLATFORM_LIMIT",
        )
    )
    age = int(inp["shop_age_months"])
    age_ok = age >= int(appetite["min_shop_age_months"])
    rules.append(_rule("R02", True, age_ok, "NONE" if age_ok else "ESCALATE", "R02_SHOP_TOO_YOUNG"))
    rate_ok = _dec(inp["return_rate_90d"]) <= _dec(appetite["max_return_rate_90d"])
    rules.append(_rule("R03", True, rate_ok, "NONE" if rate_ok else "ESCALATE", "R03_RETURN_RATE"))
    tenor_ok = 1 <= tenor <= int(cfg["cashflow"]["max_tenor_weeks"])
    rules.append(_rule("R04", True, tenor_ok, "NONE" if tenor_ok else "ESCALATE", "R04_TENOR_CAP"))

    threshold = _dec(routing["statement_request_multiple"]) * _dec(limit)
    needs_statement = _exceeds(_dec(requested), threshold, routing["statement_compare"])
    if needs_statement and statement is None:
        rules.append(_rule("R05", False, False, "REQUIRE_STATEMENT", "R05_STATEMENT_REQUIRED"))
    else:
        rules.append(_rule("R05", False, True, "NONE", "NONE"))

    if not needs_statement or statement is None:
        rules.append(_rule("R06", False, True, "NOT_APPLICABLE", "NONE"))
        mismatch = (None, None)
    else:
        lines = int(statement["platform_lines_total"])
        payout = int(statement["platform_payout"])
        if payout == 0:
            num, den = (1, 1) if lines else (0, 1)
        else:
            num, den = abs(lines - payout), payout
        mismatch = (num, den)
        ratio = _dec(num) / _dec(den)
        mismatch_limit = _dec(routing["statement_mismatch_ratio"])
        mismatched = _exceeds(ratio, mismatch_limit, routing["statement_mismatch_compare"])
        off_platform = bool(statement.get("excess_depends_on_off_platform"))
        if mismatched:
            rules.append(_rule("R06", True, False, "ESCALATE", "R06_MISMATCH"))
        elif off_platform:
            rules.append(_rule("R06", True, False, "ESCALATE", "R06_OFF_PLATFORM_NOT_EVIDENCE"))
        else:
            rules.append(_rule("R06", True, True, "NONE", "NONE"))

    cic = inp.get("cic_group")
    cic_limit = int(appetite["max_cic_group_pass"])
    cic_ok = isinstance(cic, int) and not isinstance(cic, bool) and cic <= cic_limit
    rules.append(_rule("R07", True, cic_ok, "NONE" if cic_ok else "ESCALATE", "R07_CIC_GROUP"))
    cap = int(cf["deduction_cap"])
    deduction_ok = deduction <= cap
    rules.append(
        _rule(
            "R08",
            True,
            deduction_ok,
            "NONE" if deduction_ok else "ESCALATE",
            "R08_DEDUCTION_CAP",
        )
    )

    hard_fail = any(rule["hard"] and not rule["passed"] for rule in rules)
    r05_fail = any(rule["rule_id"] == "R05" and not rule["passed"] for rule in rules)
    if hard_fail:
        status = "ESCALATED"
        fast = False
    elif r05_fail:
        status = "AWAITING_STATEMENT"
        fast = False
    else:
        status = "PENDING_REVIEW"
        fast = not needs_statement
    failed = [rule["rule_id"] for rule in rules if not rule["passed"]]
    reasons = [rule["reason_code"] for rule in rules if not rule["passed"]]
    return {
        "status": status,
        "is_fast_track": fast,
        "statement_required": needs_statement,
        "rules_evaluated": [rule["rule_id"] for rule in rules],
        "failed_rules": failed,
        "reason_codes": reasons,
        "mismatch_num": mismatch[0],
        "mismatch_den": mismatch[1],
    }


def _blank() -> dict:
    return {
        "payout_base": None,
        "payout_stress": None,
        "gap": None,
        "negative_weeks": [],
        "balances": [],
        "amount_proposed": 0,
        "tenor_weeks": 0,
        "weekly_deduction": 0,
        "tenor_trace": [],
        "deduction_cap": None,
        "payout_warning_threshold": None,
        "warning_at_threshold_min_end": None,
        "warning_below_has_gap": None,
        "status": None,
        "is_fast_track": False,
        "cic_called": False,
        "pd_scored": False,
        "rules_evaluated": [],
        "failed_rules": [],
        "reason_codes": [],
        "statement_required": False,
        "status_before_statement": None,
        "mismatch_num": None,
        "mismatch_den": None,
        "underwriting_started": False,
        "r08_passed": None,
        "active_version_status": None,
        "auto_rejected": False,
        "auto_applied": False,
        "d22": False,
    }


def _gate(inp: dict, cfg: dict) -> bool:
    cap = cfg["input_hard_cap"]
    requested = inp.get("requested_amount")
    if requested is not None and int(requested) > int(cap["max_amount_requested_vnd"]):
        return True
    note = str(inp.get("note") or "").casefold()
    if len(note) > int(cap["max_note_chars"]):
        return True
    if any(marker in note for marker in INJECTION_MARKERS):
        return True
    if any(marker in note for marker in SENSITIVE_MARKERS):
        return True
    return bool(inp.get("sensitive_attributes"))


def _monitor(inp: dict, cfg: dict) -> dict:
    out = _blank()
    payout = int(inp["current_week_payout"])
    deduction = int(inp["active_weekly_deduction"])
    cap = _trunc_mul(payout, cfg["cashflow"]["deduction_cap_ratio"])
    passed = deduction <= cap
    out["deduction_cap"] = cap
    out["r08_passed"] = passed
    out["rules_evaluated"] = ["R08"]
    out["active_version_status"] = inp.get("active_version_status")
    out["auto_applied"] = False
    out["auto_rejected"] = False
    if passed:
        out["status"] = "MONITORING_ACTIVE"
        return out
    out["status"] = "RESTRUCTURE_PROPOSED"
    out["failed_rules"] = ["R08"]
    out["reason_codes"] = ["R08_DEDUCTION_CAP"]
    out["d22"] = True
    return out


def score_case(inp: dict) -> dict:
    cfg = parameters()
    if inp.get("mode") == "monitor":
        return _monitor(inp, cfg)
    out = _blank()
    if _gate(inp, cfg):
        out["status"] = "BLOCKED_INPUT"
        return out
    cf = _cashflow(inp, cfg)
    for key in (
        "payout_base",
        "payout_stress",
        "gap",
        "negative_weeks",
        "balances",
        "amount_proposed",
        "tenor_weeks",
        "weekly_deduction",
        "tenor_trace",
        "deduction_cap",
        "payout_warning_threshold",
        "warning_at_threshold_min_end",
        "warning_below_has_gap",
    ):
        out[key] = cf[key]
    if cf["gap"] == 0:
        out["status"] = "NO_LOAN_NEEDED"
        return out
    statement = inp.get("statement")
    decision = _evaluate(cf, inp, cfg, statement)
    out.update(decision)
    if statement is not None:
        out["status_before_statement"] = _evaluate(cf, inp, cfg, None)["status"]
    out["cic_called"] = True
    out["pd_scored"] = True
    out["underwriting_started"] = True
    return out


def forbidden_words() -> tuple[str, ...]:
    text = GUARDRAILS.read_text(encoding="utf-8")
    section = text.split("### Từ cấm", 1)[1].split("###", 1)[0]
    return tuple(word.casefold() for word in re.findall(r"`([^`]+)`", section))


def lexicon_hits(text: str) -> list[str]:
    folded = text.casefold()
    return [word for word in forbidden_words() if word in folded]


def _ints(node: object, found: set[int]) -> None:
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, int):
        found.add(node)
    elif isinstance(node, float) and node.is_integer():
        found.add(int(node))
    elif isinstance(node, dict):
        for value in node.values():
            _ints(value, found)
    elif isinstance(node, list):
        for value in node:
            _ints(value, found)


def _numbers(text: str) -> list[int]:
    found = []
    for match in re.finditer(r"\d{1,3}(?:\.\d{3})+|\d+", text):
        found.append(int(match.group().replace(".", "")))
    return found


def _contradicts(sentence: str, status: str) -> bool:
    folded = sentence.casefold()
    approved = ("đã duyệt", "được duyệt", "accepted")
    if status != "ACCEPTED" and any(token in folded for token in approved):
        return True
    rejected = ("đã từ chối", "rejected")
    if status != "REJECTED" and any(token in folded for token in rejected):
        return True
    if "không cần vay" in folded and status != "NO_LOAN_NEEDED":
        return True
    needs_loan = "cần vay" in folded and "không cần vay" not in folded
    return needs_loan and status == "NO_LOAN_NEEDED"


def score_memo(case: dict) -> dict:
    sentence = case["sentence"]
    evidence_ids = list(case.get("evidence_ids") or [])
    evidence = case.get("evidence") or {}
    numbers = _numbers(sentence)
    known: set[int] = set()
    for evidence_id in evidence_ids:
        _ints(evidence.get(evidence_id), known)
    reasons: list[str] = []
    if lexicon_hits(sentence):
        reasons.append("lexicon")
    cited = [evidence_id for evidence_id in evidence_ids if evidence_id not in evidence]
    if numbers and (not evidence_ids or cited):
        reasons.append("missing_evidence")
    elif numbers and not set(numbers) <= known:
        reasons.append("number")
    if _contradicts(sentence, case.get("status_target", "")):
        reasons.append("contradicts_status")
    return {"passed": not reasons, "reasons": reasons}
