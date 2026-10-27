# Eval

Nhãn lấy từ `policy/worked-examples.md`, tức là hành vi của code đang chạy.

`api/tests/test_golden.py` đọc thư mục này. `eval/score.py` dựng một `UnderwritingService` thật (repository bộ nhớ, `BaselineRiskModel`, CIC mock lấy nhóm nợ từ nhãn, `InternalLosAdapter`), gọi `create`, rồi `submit_statement` nếu nhãn có sao kê. Không gọi mạng, không gọi model. Test còn kiểm `policy/parameters.yaml` khớp `CashFlowConfig`, `PolicyConfig`, `Plan`.

## Case

| File | Nhãn |
|---|---|
| `fx_a_no_loan.json` | Gap 0, `NO_LOAN_NEEDED`, ngưỡng cảnh báo 20.000.000/tuần. Không CIC, không PD, không rule. |
| `fx_b_gap.json` | Gap 80.000.000, đề xuất 90.000.000, 8 tuần, trích 11.250.000. `PENDING_REVIEW`. |
| `fx_c_statement.json` | Xin 300.000.000, mức sàn 100.000.000: `AWAITING_STATEMENT`. Sao kê lệch thì `ESCALATED`, khớp thì `PENDING_REVIEW`. Biến thể `boundary` khóa hành vi bằng mốc 2,5 lần không đòi sao kê. |
| `fx_e_hard_rules.json` | CIC nhóm 3, shop 5 tháng, vượt trần 100 triệu vay điện tử → `ESCALATED`. CIC nhóm 2 đạt. Hoàn 20% và dư nợ cao chỉ là rule mềm. |
| `fx_f_input_gate.json` | Ép duyệt, thuộc tính nhạy cảm, SĐT → `BLOCKED_INPUT`. Xin trên 300.000.000 → `INVALID_INPUT` (422 Pydantic). |
| `planned/` | FX-D tiền ngoài sàn, FX-L giám sát. Chưa có code, test không đọc. |

`make memo-gate` chạy mọi case trên qua `UnderwritingService` thật, kể cả bước nộp sao kê, so mọi trường trong nhãn, in câu tờ trình cuối và trả exit 1 khi lệch nhãn hoặc tờ trình không qua validator. Thêm `ARGS="--case FX-B"` để chạy một case. Không gọi model: tờ trình do code viết.

## Đo LLM thật

`make llm-eval` gọi model thật cho Copilot, không cache, nên mỗi lần chạy tốn tiền API (vài chục lượt gọi). Cần `OPENAI_API_KEY` hoặc `OPENROUTER_API_KEY` trong `api/.env` như khi chạy API.

| Phần | Đo gì |
|---|---|
| `copilot_extract` | 12 tin nhắn có nhãn ở `golden/copilot/extract.json`. Mỗi trường là `correct`, `missed` (bỏ trống), `wrong` (điền sai số) hoặc `invented` (nhãn trống mà vẫn điền). |
| `copilot_ask` | 5 câu hỏi chuẩn trên mỗi case golden. Đếm câu trả lời LLM qua được guard và mã guard khi phải lùi về template. |

`ARGS="--section ask"` chạy một phần. `ARGS="--case FX-B"` chạy một case. `ARGS="--judge"` gọi thêm Jev chấm 4 câu hỏi ngữ nghĩa trong `knowledge/prompts/jev-eval-judge.md` cho mọi câu LLM viết, cờ bật khi xác suất YES trên 0,5.

Kết quả ở `eval/out/<thời điểm>/`: `report.json` là số tổng, `review.csv` là từng câu kèm bằng chứng và cột `human_ok` trống để người chấm tay. Validator và Jev chỉ bắt được lỗi có quy tắc. Lỗi nghĩa không có số chỉ người đọc mới chắc.

`api/tests/test_golden.py::test_memo_validator_catches_injected_errors` tiêm 5 kiểu lỗi trong `eval/inject.py` (đổi chỗ hai số, sửa một số, bỏ một số, chèn "đã được duyệt", chèn "gian lận") vào mọi câu tờ trình golden, chạy trong `make test`. Chạy hai chế độ: tờ trình mới (có semantic frame, chữ phải đúng mẫu) và tờ trình cũ trên prod (chỉ kiểm số, thứ tự số, từ cấm, trạng thái).

`golden/llm/` khóa validator chữ: số phải có trong bằng chứng, không dùng từ trong `knowledge/guardrails.md`, không viết đã duyệt hay đã chấp thuận khi trạng thái chưa phải `ACCEPTED`, câu có `locked_text` phải đúng nguyên văn (tráo số, sai chủ thể, số bằng chữ, phí bịa). Jev chấm ngữ nghĩa ở ngoài CI, theo `knowledge/prompts/jev-eval-judge.md`.

## Thêm case

Chép một file, đổi `case_id`, điền số đã tính tay theo `policy/worked-examples.md`. Không điền 45.000.000 để khớp slide. Chạy `make test`.
