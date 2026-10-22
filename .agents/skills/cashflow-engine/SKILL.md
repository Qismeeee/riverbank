---
name: cashflow-engine
description: Sửa hoặc mở rộng CashFlow Engine (gap, số tiền đề xuất, kỳ hạn, kỳ trích) trong api/services/cashflow.py. Dùng khi đụng tới FLOW-03, FR-08 đến FR-15, hoặc khi số trong demo lệch với docs.
---

# CashFlow Engine

Nguồn chuẩn: `docs/Riverbank-TDD.md` FLOW-03 và FLOW-03a. Code: `api/services/cashflow.py` (`CashFlowEngine(config).compute(CashFlowInput) -> CashFlowResult`). Test: `api/tests/test_cashflow.py`, `api/tests/test_golden.py`.

## Thuật toán, đúng thứ tự

1. `payout_base` = median lịch sử payout tuần (tối thiểu 4 tuần). Không để LLM chọn.
2. `payout_stress = payout_base * 0.7`.
3. Tuần 0 nhập hàng, payout vào từ tuần 1. `bal_end[w] = bal_start[w] + payout[w] - outflow[w]`.
4. `gap = abs(min(bal_end))` nếu có tuần âm, ngược lại 0 và dừng (FLOW-03a, tính `payout_warning_threshold`).
5. `amount = round_up_to_5M(gap * 1.1)` rồi `min(amount, gap * 1.15)`.
6. `tenor = (tuần âm cuối - tuần âm đầu + 1) + 2`.
7. `deduction = amount // tenor`. Khi `deduction > 0.25 * payout_stress` và `tenor < 26`: `tenor += 2`, tính lại. Lưu từng bước vào `tenor_trace` để memo giải thích.

## Số vàng

Case B: cash 120, supplier 200 tuần 0, payout 70 → stress 49, gap 80, âm tuần 0 và 1, amount 88 → 90, tenor 4 → 6 → 8, deduction 11,25 triệu.
Case A: gap 0, ngưỡng cảnh báo 20 triệu/tuần.

## Khi sửa

- Đổi hằng số: truyền `CashFlowConfig(...)` vào engine hoặc sửa mặc định trong `CashFlowConfig`, không sửa số rải trong hàm.
- Biến thể công thức: kế thừa `CashFlowEngine` và ghi đè `_propose` hoặc `_balances`, giữ `compute` nguyên.
- Đổi công thức: sửa TDD trước, rồi code, rồi golden, cùng một PR, ghi lý do.
- Tiền là `int` VND. Không `round()`, dùng `//` và `round_up_to_unit`.
- Không gọi LLM, không đọc env, không đọc DB trong file này. Caller chuẩn bị `CashFlowInput`.
- Mỗi lần chạy sinh một `evidence_id` (E5) ở lớp gọi, lưu `weeks` và `tenor_trace` vào evidence store.

## Kiểm tra

```
make test
```
`test_case_b_numbers_match_docs` và `test_golden_case[case_b_loan_90m]` phải xanh. Nếu đỏ mà bạn không chủ ý đổi công thức thì bạn đã làm sai.
