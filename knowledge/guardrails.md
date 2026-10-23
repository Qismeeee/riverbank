# Guardrail ezXRiverbank

Hai chiều: chặn trước khi model đọc, và chặn trước khi người đọc.

Trạng thái code (23/10/2026): chiều vào là một regex trong `api/services/underwriting/service.py`, gặp lệnh ép duyệt, SĐT, CCCD, số tài khoản hay thuộc tính nhạy cảm thì `422 BLOCKED_INPUT` cả hồ sơ, chưa che rồi cho đi tiếp, chưa gọi Jev. Chiều ra là `MemoValidator` (`api/services/underwriting/memo.py`) dùng `OutputGuard` (`api/services/guard.py`): claims phải đúng payload bằng chứng, số trong câu phải nằm trong claims, đủ mọi số nguyên của claims, không từ cấm, không nói ngược trạng thái. Bước Jev chưa có. Phần còn lại của file này là thiết kế cho bước nối LLM. Ngưỡng xác suất nằm ở `policy/parameters.yaml` (`guardrail.block_probability` = 0,5).

## Chiều vào

Chạy đúng thứ tự. Bước sau không được gọi khi bước trước đã chặn.

1. **Che PII tại máy chủ**, trên chữ tự do, trước mọi lệnh gọi Jev hoặc OpenRouter.
2. **Trần cứng** trên các số có cấu trúc. Vượt trần → `BLOCKED_INPUT`. Không gọi model để "chỉnh số cho hợp lệ".
3. **Jev** trên chữ đã che: prompt injection và thuộc tính nhạy cảm. Xác suất ≥ 0,5 → `BLOCKED_INPUT`.
4. Chỉ phần còn lại mới được đưa vào LLM trích xuất hoặc thẩm định.

### PII

- SĐT Việt Nam → `[REDACTED_PHONE]`. Mẫu: `\b(?:\+?84|0)(?:3|5|7|8|9)\d{8}\b`
- 12 chữ số không đứng cạnh `tr`, `triệu`, `tỷ`, `vnd`, `đ` → `[REDACTED_NATIONAL_ID]`. Mẫu: `\b\d{12}\b`
- 8–16 chữ số cạnh `tk`, `stk`, `tài khoản`, `ngân hàng` → `[REDACTED_ACCOUNT]`

Che trên bản sao đưa ra ngoài. Hồ sơ nội bộ giữ `sanitized_note` đã che, không giữ số gốc trong payload model.

### Trần cứng

`max_amount_requested_vnd`, `max_lot_value_vnd`, `max_cash_on_hand_vnd`, `max_sell_weeks`, `max_supplier_weeks`, `max_note_chars` trong `parameters.yaml`.

Model trích xuất phải trả lại đúng số seller nói. Việc từ chối là của cổng nhập.

### Injection

Coi là injection khi chữ yêu cầu đổi luật hệ thống: bỏ qua chính sách, ép duyệt, đặt trạng thái `ACCEPTED`, tiết lộ prompt, giả danh người duyệt. Ngưỡng Jev đạt thì chặn cả lượt, không "làm sạch rồi cho đi tiếp".

### Thuộc tính nhạy cảm

Giới tính, tôn giáo, dân tộc, tình trạng sức khỏe. Chúng không được vào feature, prompt thẩm định, SHAP, hay rule. Jev gắn có mặt với xác suất ≥ 0,5 thì `BLOCKED_INPUT`.

## Chiều ra

Áp dụng cho `D09`, `D10`, câu trả lời Copilot, và đề xuất `D22`.

1. **Python:** câu có số mà thiếu `evidence_id` → fail. Số trong câu lệch dù 1 đồng so với payload bằng chứng được trích → fail.
2. **Python:** gặp từ cấm → fail.
3. **Jev:** câu có ám chỉ gian lận dù không dùng từ cấm, hoặc lời văn bảo duyệt thẳng hồ sơ đang `ESCALATED` / rule đang fail → fail.
4. Fail thì sinh lại. Hết `memo.max_regen_attempts` (2) → lần kế tiếp chuyển `ESCALATED`, đóng nhãn `UNVERIFIED_DRAFT`, không đưa bản đó thành tờ trình chính thức.

### Từ cấm

`gian lận`, `lừa đảo`, `giả mạo`, `chiếm đoạt`, `fraud`, `scam`.

Chỗ đó chỉ được viết: số liệu không khớp, cần xác minh.

### Số và bằng chứng

Mã bằng chứng khóa:

| Mã | Nội dung |
|---|---|
| E4 | Snapshot feature: payout tuần, tuổi shop, tỷ lệ hoàn, `platform_supported_amount`, nguồn dữ liệu. Code gộp E1 đến E4 cũ vào đây |
| E5 | Dòng tiền, Gap, đề xuất, lịch trích |
| E6 | PD và yếu tố giải thích. Chỉ khi Gap > 0 |
| E6a | Kết quả CIC qua cổng ngân hàng. Chỉ khi Gap > 0 |
| E7 | Kết quả từng rule |
| E8 | Đối chiếu sao kê. Chỉ khi đã nhập sao kê |

`D09` gắn mã trong câu. `D10` và Copilot nói tiếng thường, không đọc mã `E*` / `D*` / `SHAP` cho seller, nhưng số vẫn phải lấy từ cùng các bảng đó.

## Việc guardrail không làm

Guardrail không sửa kết quả rule, không hạ số tiền vay, không đổi Gap, không tự chuyển hồ sơ sang `ACCEPTED` hoặc `REJECTED`.
