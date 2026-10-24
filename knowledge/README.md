# Knowledge ezXRiverbank

Bộ này là nguồn cho prompt và guardrail của sản phẩm. Từ 23/10/2026 số và rule lấy theo code đang chạy, xem `AGENTS.md`.

## Thứ tự hiệu lực

1. Code: `api/services/cashflow.py`, `api/policy/engine.py`, `api/policy/rules.py`, `api/services/underwriting/`.
2. `policy/parameters.yaml`, bản chép ngưỡng của code, có test kiểm khớp.
3. `policy/policy.md`, ý nghĩa rule R01 đến R10 và nhánh trạng thái.
4. `policy/worked-examples.md`, nhãn kiểm thử tính tay.
5. `docs/Riverbank-SRS.md`, `docs/Riverbank-TDD.md`, `docs/Riverbank-BRD.md`.
6. Slide. Số trong slide không đè công thức.

Hai số khóa:

- Shop median 70.000.000/tuần, trả 100% tuần 0: đề xuất **90.000.000**, kỳ hạn **8**, trích **11.250.000**/tuần.
- Cọc 50% rồi trả nốt tuần 4, cùng payout: Gap = 0, ngưỡng cảnh báo **20.000.000**/tuần. Không hard-code 45 triệu.

## Policy

| File | Việc |
|---|---|
| `policy/policy.md` | R01 đến R10, thứ tự xử lý, quyết định của người, phần chưa có code |
| `policy/parameters.yaml` | Hệ số, trần, ngưỡng so sánh |
| `policy/worked-examples.md` | FX-A, FX-B, FX-C, FX-E, FX-F, duyệt số khác. FX-D, FX-L chưa có code |

`rules.py` đã có và chạy trên prod. Prompt nào nhắc ngưỡng phải lấy đúng số trong `parameters.yaml`.

Trạng thái hiện tại: `memo-writer.md` được `api/prompts.py` nạp khi `RIVERBANK_MEMO_WRITER=llm`. Các prompt khác là kế hoạch. Thư mục này được đóng vào image API tại `/app/knowledge`, đổi prompt là đổi `prompt_version` trong tờ trình.

## Prompt

Mỗi file trong `knowledge/prompts/` là system prompt đóng sẵn. Gọi model đúng bậc: số và pass/fail không đi qua LLM.

| Prompt | Bậc | Khi nào |
|---|---|---|
| `copilot-extract.md` | LLM nhỏ | Seller chat ngôn ngữ tự nhiên, trước khi tạo hồ sơ |
| `copilot-qa.md` | LLM nhỏ | Seller hỏi lại trên số đã khóa |
| `memo-writer.md` | LLM nhỏ | Sinh D09 và D10 |
| `monitor-proposal.md` | LLM nhỏ | Viết D22 sau khi engine đã tính phương án giãn |
| `statement-ocr.md` | Vision | PDF/ảnh sao kê, chỉ bóc dòng |
| `jev-input-guard.md` | Jev | Sau khi đã che PII |
| `jev-output-guard.md` | Jev | Sau validator số của Python |
| `jev-line-classifier.md` | Jev | Gắn dòng sao kê có phải payout sàn |
| `policy-explainer.md` | LLM nhỏ | Giải thích kết quả rule đã chạy, không chạy lại rule |
| `data-analyst.md` | LLM nhỏ | Câu hỏi đột xuất của người duyệt, chỉ SELECT |
| `jev-eval-judge.md` | Jev | Chấm golden set về ngữ nghĩa, không chấm lại Gap |

Ràng buộc dùng chung: `knowledge/guardrails.md`. Tên trạng thái: `knowledge/glossary.md`. Quy ước khi viết code: `AGENTS.md`.

## Rule cho agent viết code

`.cursor/rules/` nhắc bất biến khi sửa repo: người quyết định, toán deterministic, policy-as-code, prompt không tính tiền, chữ trên UI seller.
