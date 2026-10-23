# AGENTS.md — ezXRiverbank

Quy ước cho mọi phiên viết code của sản phẩm. Từ 23/10/2026 số và rule theo code đang chạy, xem `AGENTS.md` và `docs/Riverbank-spec-vs-code.md`. Ngưỡng số nằm ở `policy/parameters.yaml`. Ý rule nằm ở `policy/policy.md`. Prompt nằm ở `knowledge/prompts/`.

## Bất biến

- Người của ngân hàng bấm thì hồ sơ mới sang `ACCEPTED`, `REJECTED`, hoặc version `RESTRUCTURED_P7` (P7 chưa có code). Không có cạnh từ `ANALYZING` tới hai trạng thái quyết định.
- Gap, số tiền, kỳ hạn, lịch trích, PD, pass/fail rule là code thuần. LLM không cộng trừ và không sửa các trường đó.
- Gap = 0 thì không gọi CIC, không chấm PD, không chạy R01 đến R09.
- Chữ tự do ra khỏi máy chủ chỉ sau khi đã che SĐT, CCCD, số tài khoản.
- Tờ trình chính thức: câu có số khớp bằng chứng, sai lệch 0 đồng. Cấm các từ trong `knowledge/guardrails.md`.
- Đổi số tiền, kỳ hạn, lịch trả là bản ghi mới. Code hiện thêm một lần chạy `trigger = COUNTER` vào `runs`, bảng version chưa có. `case_events` chỉ thêm.
- LLM không viết câu chữ hợp đồng, khế ước, ủy nhiệm chi.
- Thiếu consent thì không đọc dữ liệu sàn. Một hồ sơ chỉ chứa dữ liệu của đúng `seller_id`.

## Chỗ nào được gọi model

| Việc | Chỗ |
|---|---|
| Trích 5 trường từ chat seller | `copilot-extract` |
| Seller hỏi lại | `copilot-qa` |
| Viết D09, D10 | `memo-writer` |
| Viết D22 | `monitor-proposal` |
| Bóc dòng sao kê PDF/ảnh | `statement-ocr` |
| Injection, thuộc tính nhạy cảm, ý gian lận, phân loại dòng sao kê, chấm ngữ nghĩa eval | Jev |

CSV sao kê đi parser Python, không đi vision. Phân loại dòng và cộng tổng là Jev rồi Python.

## Policy-as-code

Sửa khẩu vị bằng `policy/policy.md` và `policy/parameters.yaml`. Bản `rules.py` sinh từ hai file đó, kèm test, pytest pass, rồi người xác nhận. Engine từ chối nạp bản chưa qua các bước này. Prompt không được nhắc một ngưỡng khác file tham số.

## Khi tài liệu lệch số

Làm theo `knowledge/README.md`. Fixture tính tay trong `policy/worked-examples.md` là nhãn test của engine. Không hard-code 90.000.000, 12.000.000 hay 45.000.000 để khớp slide.

## Việc không thêm

Tự duyệt, tự từ chối, tự giãn nợ, kết luận gian lận, đọc CIC khi Gap = 0, đưa thuộc tính nhạy cảm vào feature, ghi đè version, xóa audit.
