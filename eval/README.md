# Eval

Nhãn lấy từ `policy/worked-examples.md`, tức là hành vi của code đang chạy.

`api/tests/test_golden.py` đọc thư mục này. `eval/score.py` dựng một `UnderwritingService` thật (repository bộ nhớ, `DemoRiskModel`, CIC mock lấy nhóm nợ từ nhãn, `MockLosAdapter`), gọi `create`, rồi `submit_statement` nếu nhãn có sao kê. Không gọi mạng, không gọi model. Test còn kiểm `policy/parameters.yaml` khớp `CashFlowConfig`, `PolicyConfig`, `Plan`.

## Case

| File | Nhãn |
|---|---|
| `fx_a_no_loan.json` | Gap 0, `NO_LOAN_NEEDED`, ngưỡng cảnh báo 20.000.000/tuần. Không CIC, không PD, không rule. |
| `fx_b_gap.json` | Gap 80.000.000, đề xuất 90.000.000, 8 tuần, trích 11.250.000. `PENDING_REVIEW`. |
| `fx_c_statement.json` | Xin 300.000.000, mức sàn 100.000.000: `AWAITING_STATEMENT`. Sao kê lệch thì `ESCALATED`, khớp thì `PENDING_REVIEW`. Biến thể `boundary` khóa hành vi bằng mốc 2,5 lần không đòi sao kê. |
| `fx_e_hard_rules.json` | CIC nhóm 3, shop 5 tháng, vượt trần 100 triệu vay điện tử → `ESCALATED`. CIC nhóm 2 đạt. Hoàn 20% và dư nợ cao chỉ là rule mềm. |
| `fx_f_input_gate.json` | Ép duyệt, thuộc tính nhạy cảm, SĐT → `BLOCKED_INPUT`. Xin trên 300.000.000 → `INVALID_INPUT` (422 Pydantic). |
| `planned/` | FX-D tiền ngoài sàn, FX-L giám sát. Chưa có code, test không đọc. |

`golden/llm/` khóa validator chữ: số phải có trong bằng chứng, không dùng từ trong `knowledge/guardrails.md`, không viết đã duyệt khi trạng thái chưa phải `ACCEPTED`. Jev chấm ngữ nghĩa ở ngoài CI, theo `knowledge/prompts/jev-eval-judge.md`.

## Thêm case

Chép một file, đổi `case_id`, điền số đã tính tay theo `policy/worked-examples.md`. Không điền 45.000.000 để khớp slide. Chạy `make test`.
