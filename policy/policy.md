# Chính sách tín dụng ezXRiverbank (bản demo)

Nguồn số: `parameters.yaml`. Ví dụ tính tay: `worked-examples.md`. Người của ngân hàng sửa hai file đó khi đổi khẩu vị. Bộ rule chỉ được nạp sau khi bản dịch `rules.py` qua pytest và được người duyệt policy xác nhận.

Chính sách này trả lời: hồ sơ có được đi tiếp, phải bổ sung sao kê, hay phải chuyển người. Chính sách không tính Gap, không chọn số tiền, không duyệt vay, không viết hợp đồng.

## 1. Ai được quyết định

| Việc | Ai làm |
|---|---|
| Gap, số đề xuất, kỳ hạn, lịch trích, PD | Code và LightGBM |
| Pass/fail từng rule | Policy Engine |
| Chấp nhận, sửa số, từ chối, duyệt giãn nợ | Người có thẩm quyền của ngân hàng |
| Điền hợp đồng, OTP, giải ngân, sổ cái | LOS và Core ngân hàng |
| Câu chữ tờ trình và giải thích | LLM, trên số đã khóa |

Không có `ACCEPTED`, `REJECTED`, hay lịch trả mới nào phát sinh khi chưa có bản ghi `human_decisions`.

## 2. Thứ tự xử lý

1. Che PII trên chữ tự do. Số vượt trần cứng hoặc chữ vi phạm guardrail → `BLOCKED_INPUT`. Dừng.
2. Thiếu consent hiệu lực (`D01`) → dừng, không đọc dữ liệu sàn.
3. Tính Gap trên kịch bản cơ sở.
4. Gap = 0 → `NO_LOAN_NEEDED`, kèm ngưỡng payout cảnh báo. Không gọi CIC, không chấm PD, không chạy rule dưới đây.
5. Gap > 0 → tra CIC qua cổng ngân hàng, chấm PD, chạy toàn bộ rule đang hiệu lực.
6. Có rule cứng fail → `ESCALATED`. Không đòi sao kê để "cứu" hồ sơ.
7. Không rule cứng nào fail, và R05 fail → `AWAITING_STATEMENT`.
8. Không rule nào fail → `PENDING_REVIEW` và `is_fast_track = true`.
9. Viết `D09`/`D10`. Guardrail đầu ra fail quá số lần cho phép → `ESCALATED`, nhãn `UNVERIFIED_DRAFT`.
10. Dừng chờ người bấm.

## 3. Việc không nằm trong rule

- Số seller xin không bao giờ là số đề xuất. Số đề xuất là đầu ra CashFlow Engine.
- PD và SHAP là giải thích cho người duyệt. Không có ngưỡng PD để tự từ chối.
- Giới tính, tôn giáo, dân tộc, tình trạng sức khỏe không có mặt trong snapshot đưa vào rule hay model.
- Lãi và phí chỉ hiện khi có biểu phí ngân hàng. Thiếu biểu phí thì ghi là đang chờ, không ước tính.
- LLM không được viết điều khoản hợp đồng, khế ước, ủy nhiệm chi.

## 4. Bộ rule

Mỗi rule đọc snapshot đã tính. Rule trả `passed`, `hard`, `effect`, `reason_code`. Rule không sửa `proposed_amount`, `tenor_weeks`, hay `weekly_deduction`.

`effect` chỉ là một trong: `NONE`, `ESCALATE`, `REQUIRE_STATEMENT`, `NOT_APPLICABLE`.

### R01 — Hạn mức dữ liệu sàn

- Cứng: có. Chạy khi Gap > 0.
- Đạt khi `proposed_amount <= platform_support_limit`.
- Fail → `ESCALATE`, `R01_ABOVE_PLATFORM_LIMIT`.
- `platform_support_limit` là trường bằng chứng E4 do Feature Service cung cấp. Bản demo của shop mẫu là 120.000.000. Policy không tự bịa hạn mức.

### R02 — Tuổi shop

- Cứng: có. Chạy khi Gap > 0.
- Đạt khi `shop_age_months >= min_shop_age_months` (6).
- Fail → `ESCALATE`, `R02_SHOP_TOO_YOUNG`.

### R03 — Tỷ lệ hoàn

- Cứng: có. Chạy khi Gap > 0.
- Đạt khi `return_rate_90d <= max_return_rate_90d` (0,08).
- Fail → `ESCALATE`, `R03_RETURN_RATE`.

### R04 — Trần kỳ hạn

- Cứng: có. Chạy khi Gap > 0.
- Đạt khi `1 <= tenor_weeks <= max_tenor_weeks` (24).
- Fail → `ESCALATE`, `R04_TENOR_CAP`.
- Việc cộng tuần để đưa khoản trích xuống trần 25% là việc của engine, trước khi rule này chạy.

### R05 — Chứng từ khi xin vượt mức

- Cứng: không. Chạy khi Gap > 0 và chưa có rule cứng nào fail.
- Đạt khi `amount_requested < statement_request_multiple × platform_support_limit`.
- So sánh là `>=`. Mốc demo: 300.000.000 so với 2,5 × 120.000.000.
- Fail → `REQUIRE_STATEMENT`, `R05_STATEMENT_REQUIRED`, trạng thái `AWAITING_STATEMENT`.
- Fail R05 không phải từ chối và không phải `ESCALATED`.

### R06 — Đối chiếu sao kê

- Cứng: có khi hồ sơ đã vào nhánh sao kê. Hồ sơ không phải nộp sao kê thì `NOT_APPLICABLE` và coi như đạt.
- `mismatch = abs(tong_dong_san - payout_san) / payout_san` trên cùng một kỳ. `payout_san = 0` mà sao kê vẫn có dòng sàn → mismatch = 1.
- Đạt khi đồng thời:
  1. `mismatch <= statement_mismatch_ratio` (0,15).
  2. Phần tiền xin vượt `platform_support_limit` không phụ thuộc các dòng ngoài sàn.
- Dòng ngoài sàn được liệt kê trong D08 và không được cộng vào hạn mức, Gap, hay khả năng trả.
- Fail lệch → `ESCALATE`, `R06_MISMATCH`.
- Fail vì tiền ngoài sàn → `ESCALATE`, `R06_OFF_PLATFORM_NOT_EVIDENCE`.
- Khớp trong ngưỡng, đề xuất gốc vẫn trong hạn mức sàn → `PENDING_REVIEW`, `is_fast_track = false`.

### R07 — Nhóm nợ CIC

- Cứng: có. Chỉ chạy khi đã có `D04`.
- Đạt khi `cic_group <= max_cic_group_pass` (1).
- Fail → `ESCALATE`, `R07_CIC_GROUP`.
- Không có bản ghi CIC vì Gap = 0 thì rule này không được gọi.

### R08 — Trần trích payout

- Cứng: có ở thẩm định. Ở giám sát sau giải ngân, fail tạo đề xuất giãn nợ chứ không tự đổi lịch.
- Thẩm định: đạt khi `weekly_deduction <= deduction_cap_ratio × stress_weekly_payout`.
- Giám sát: đạt khi khoản trích của version đang `ACTIVE` `<= deduction_cap_ratio × payout_tuan_hien_tai`.
- Fail lúc thẩm định → `ESCALATE`, `R08_DEDUCTION_CAP`. Engine không được âm thầm hạ số tiền để rule này trở thành đạt.
- Fail lúc giám sát → tạo `D22`, trạng thái `RESTRUCTURE_PROPOSED`. Version đang chạy vẫn `ACTIVE` cho đến khi người duyệt.

## 5. Nhánh trạng thái sau rule

| Điều kiện | Trạng thái | Fast-track |
|---|---|---|
| Gap = 0 | `NO_LOAN_NEEDED` | không |
| Rule cứng fail, hoặc R06 fail, hoặc memo fail lần 3 | `ESCALATED` | không |
| Chỉ R05 fail | `AWAITING_STATEMENT` | không |
| Mọi rule áp dụng đều đạt, không qua sao kê | `PENDING_REVIEW` | có |
| R06 đạt sau sao kê | `PENDING_REVIEW` | không |

`ESCALATED` chỉ sang `ACCEPTED` khi quyết định có `verification_note`. `NO_LOAN_NEEDED` không biến thành khoản vay trên cùng một kế hoạch.

## 6. Quyết định của người

Người gọi API quyết định phải có vai trò thẩm quyền ngân hàng. Tài khoản seller và tác nhân hệ thống bị từ chối.

| Quyết định | Hệ quả |
|---|---|
| `ACCEPT_PROPOSAL` | Giữ version đang đề xuất. Sang `ACCEPTED`. |
| `ACCEPT_MODIFIED` | Tính lại trần trích 25%. Thêm version mới `HUMAN_MODIFIED` / `ACTIVE`. Version trước thành `SUPERSEDED`. |
| `REJECT` | Sang `REJECTED`. Gói sang LOS không có số tiền giải ngân. |
| `APPROVE_RESTRUCTURE` | Thêm version `RESTRUCTURED_P7` / `ACTIVE`. Không ghi đè lịch cũ. |

Sửa số tiền hoặc kỳ hạn luôn là bản ghi mới. Nhật ký `case_events` chỉ thêm, không sửa và không xóa.

## 7. Hợp đồng để dịch sang code

Mỗi rule là một hàm thuần, cùng snapshot cho ra cùng kết quả. Lời văn không được ghi vào input của hàm này.

```python
def evaluate_all_rules(snapshot: CaseSnapshot) -> PolicyEvaluation:
    """Mỗi phần tử: rule_id, passed, hard, effect, reason_code."""
```

`effect = ESCALATE` chỉ đặt cờ đích cho người duyệt. Hàm này không ghi `REJECTED`.

Bộ rule chưa có pytest pass và chưa có người xác nhận phiên bản thì Policy Engine từ chối nạp.
