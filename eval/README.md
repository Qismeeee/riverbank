# Eval

Nhãn lấy từ `policy/worked-examples.md`. Ngưỡng lấy từ `policy/parameters.yaml`. Khi slide hoặc TDD trong `docs/` kể khác công thức, hai file đó thắng.

`api/tests/test_golden.py` chấm thư mục này bằng `eval/score.py`. Không gọi mạng, không gọi model.

## Case

| File | Nhãn |
|---|---|
| `fx_a_no_loan.json` | Gap 0, `NO_LOAN_NEEDED`, ngưỡng cảnh báo 20.000.000/tuần. Không CIC, không PD, không R01–R08. |
| `fx_b_gap.json` | Gap 80.000.000, đề xuất 88.000.000, 8 tuần, trích 11.000.000. `PENDING_REVIEW`, fast-track. |
| `fx_c_statement.json` | Xin 300.000.000 nên cần sao kê, lệch 480/280 thì `ESCALATED`, lý do `R06_MISMATCH`. |
| `fx_d_off_platform.json` | Dòng sàn khớp, phần vượt hạn mức dựa vào tiền ngoài sàn. `R06_OFF_PLATFORM_NOT_EVIDENCE`. |
| `fx_e_hard_rules.json` | CIC nhóm 2, shop dưới 6 tháng, hoặc hoàn trên 8% → `ESCALATED`. Không tự `REJECTED`. |
| `fx_f_input_gate.json` | Ép duyệt, thuộc tính nhạy cảm, hoặc số xin trên 1.000.000.000 → `BLOCKED_INPUT`. |
| `fx_l_monitor.json` | Trích đang chạy 12.000.000, payout tuần 42.000.000, trần mới 10.500.000. `RESTRUCTURE_PROPOSED`, lịch `ACTIVE` giữ nguyên. |

`golden/llm/` khóa validator chữ: số phải có trong bằng chứng, không dùng từ trong `knowledge/guardrails.md`, không viết đã duyệt khi trạng thái chưa phải `ACCEPTED`. Jev chấm ngữ nghĩa ở ngoài CI, theo `knowledge/prompts/jev-eval-judge.md`.

## Thêm case

Chép một file, đổi `case_id`, điền số đã tính từ `parameters.yaml`. Không điền 90.000.000, 11.250.000 hay 45.000.000 để khớp slide. Chạy `make test`.
