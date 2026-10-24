---
name: policy-rules
description: Thêm, sửa hoặc đổi ngưỡng rule chính sách trong api/policy (R01 đến R10). Dùng khi đụng FR-17 đến FR-20, FR-32, BL-01 đến BL-11, hoặc khi người duyệt cần lý do rule fail.
---

# Policy Engine

Nguồn: `api/policy/policy.md` (bảng rule, ngưỡng, loại hard/soft). Engine: `api/policy/engine.py` (`PolicyEngine`, `Rule`, `PolicyInput`, `PolicyConfig`). Rule: `api/policy/rules.py` (hàm + `RULES` + `default_engine()`). Test: `api/tests/test_rules.py`.
`policy.md`, `rules.py`, `test_rules.py` luôn đi cùng nhau trong một PR. `engine.py` hiếm khi đổi.

## Hợp đồng

- Mỗi rule là hàm `rXX_ten(p: PolicyInput, c: PolicyConfig) -> RuleResult`, đăng ký bằng `Rule("RXX", hard, fn)` trong `RULES` theo thứ tự ghi ở policy.md.
- Dùng engine: `default_engine().evaluate(PolicyInput(...))` trả `PolicyOutcome` (`hard_fail`, `soft_warnings`, `failed_rule_ids`). Bộ rule khác hoặc ngưỡng khác: `PolicyEngine(rules, PolicyConfig(...))`.
- `hard=True` fail thì hồ sơ Escalated, API trả 409 nếu người duyệt cố lưu. `hard=False` chỉ là cảnh báo trong memo.
- Thiếu dữ liệu cho rule hard: trả `_missing(...)`, không đoán, không mặc định pass.
- `reason` là một câu tiếng Việt có số, Memo Writer dùng nguyên văn.
- Ngưỡng nằm trong `PolicyConfig`, có dấu (*) trong policy.md nếu là giá trị minh họa.

## Thêm rule

1. Thêm dòng vào bảng `policy.md`: mã, loại, nội dung, ngưỡng, nguồn BL/BR.
2. Viết hàm trong `rules.py`, thêm `Rule(...)` vào `RULES`.
3. Viết test pass, test fail, test thiếu dữ liệu.
4. `make test`.

## Duyệt số khác (FR-32)

Router quyết định phải gọi `CashFlowEngine().compute` với số mới rồi `default_engine().evaluate` lại. `hard_fail` thì không lưu quyết định. Lưu `policy_results_final` cùng quyết định để audit.

## Không làm

Không để LLM sinh rule lúc chạy. Codex sinh code rule lúc dev là được, nhưng người review và test mới là bằng chứng.
