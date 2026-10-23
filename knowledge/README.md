# Knowledge ezXRiverbank

Bộ này là nguồn cho prompt, rule và policy của sản phẩm. BRD/SRS/TDD giữ nguyên vai trò tài liệu gốc. Khi số liệu lệch nhau, thứ tự bên dưới thắng.

## Thứ tự hiệu lực

1. `policy/parameters.yaml` — mọi ngưỡng số.
2. `policy/policy.md` — ý nghĩa rule và nhánh trạng thái.
3. `policy/worked-examples.md` — nhãn kiểm thử tính tay.
4. `ezXRiverbank-SRS.md` — hành vi phải đạt.
5. `ezXRiverbank-TDD.md` — luồng kỹ thuật và API.
6. `ezXRiverbank-BRD.md` — ý nghiệp vụ.
7. `riverbank-architecture.md` — cách kể trên sân khấu. Số trong slide không đè công thức.

Hai chỗ đã chốt vì tài liệu kể khác công thức:

- Shop median 70.000.000/tuần, trả 100% tuần 0: đề xuất **88.000.000**, kỳ hạn **8**, trích **11.000.000**/tuần. Không hard-code 90 triệu và 12 triệu.
- Cọc 50% rồi trả nốt tuần 4, cùng payout: Gap = 0, ngưỡng cảnh báo **20.000.000**/tuần. Không hard-code 45 triệu.

## Policy

| File | Việc |
|---|---|
| `policy/policy.md` | R01–R08, thứ tự xử lý, quyết định của người |
| `policy/parameters.yaml` | Hệ số, trần, ngưỡng so sánh |
| `policy/worked-examples.md` | FX-A, FX-B, FX-C, FX-D, FX-E, FX-F, FX-L |

Chưa có `rules.py`. Policy Engine chỉ nạp bản dịch sau pytest và xác nhận người. Codex dịch từ `policy.md`, không sửa pass/fail bằng lời văn.

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
