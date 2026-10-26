# Chính sách tín dụng Riverbank (bản demo)

Bản này mô tả đúng bộ rule đang chạy trên prod: `api/policy/rules.py` (`policy_version = riverbank-r01-r10-v4`). Ngưỡng ở `parameters.yaml`, ví dụ tính tay ở `worked-examples.md`. Bảng tóm tắt cho dev nằm ở `api/policy/policy.md`.

Đổi khẩu vị thì sửa `PolicyConfig` trong `api/policy/engine.py`, `parameters.yaml` và file này trong cùng một PR. `api/tests/test_golden.py` kiểm `parameters.yaml` khớp code và mỗi rule trong code có mục ở đây.

Chính sách trả lời: hồ sơ được đi tiếp, phải bổ sung sao kê, hay phải chuyển người. Chính sách không tính Gap, không chọn số tiền, không duyệt vay, không viết hợp đồng.

## 1. Ai được quyết định

| Việc | Ai làm |
|---|---|
| Gap, số đề xuất, kỳ hạn, lịch trích | `CashFlowEngine` |
| PD và yếu tố giải thích | `RiskModel`. Bản prod là `LightGbmRiskModel`, xem mục 3 |
| Pass/fail từng rule | `PolicyEngine` |
| Chấp nhận, sửa số, từ chối | Người có vai trò `underwriter` |
| Hợp đồng, OTP, giải ngân, sổ cái | LOS và Core ngân hàng |
| Câu chữ tờ trình và bản seller | Template có sẵn, điền số từ bằng chứng |

Không có `ACCEPTED` hay `REJECTED` khi chưa có quyết định ghi qua `POST /v1/cases/{id}/decisions`.

## 2. Thứ tự xử lý trong `POST /v1/cases`

1. Pydantic kiểm kiểu và trần cổng nhập. Sai thì `422`, không tạo hồ sơ.
2. Regex quét `note`: lệnh bỏ qua chính sách hoặc ép duyệt, SĐT, CCCD 12 số, số tài khoản, thuộc tính nhạy cảm. Có thì `422 BLOCKED_INPUT`, không tạo hồ sơ.
3. Thiếu consent hiệu lực → `422 CONSENT_REQUIRED`, không đọc dữ liệu sàn. Snapshot feature sai → lỗi `422` hoặc `403` tương ứng.
4. Tính Gap trên kịch bản cơ sở.
5. Gap = 0 → `NO_LOAN_NEEDED`, kèm ngưỡng payout cảnh báo. Không gọi CIC, không chấm PD, không chạy rule.
6. Gap > 0 → chấm PD (E6), qua Jev gate rồi mới tra CIC (E6a, xem mục 3), chạy đủ mười rule (E7).
7. Có rule cứng fail khác R05 thiếu sao kê, hoặc sao kê lệch → `ESCALATED`, lý do `HARD_POLICY_FAILURE`.
8. Thiếu dữ liệu hồ sơ shop nên không chấm được PD → `ESCALATED`, lý do `RISK_DATA_MISSING`.
9. Rule cứng duy nhất fail là R05 vì thiếu kết quả sao kê → `AWAITING_STATEMENT`, lý do `STATEMENT_REQUIRED`.
10. Còn lại → `PENDING_REVIEW`. Rule mềm fail chỉ hiện trong kết quả rule.
11. Sinh tờ trình và bản seller từ template, kiểm từng câu với bằng chứng, tối đa 3 lần. Không qua → `ESCALATED`, lý do `MEMO_VALIDATION_FAILED`.
12. Dừng chờ người bấm.

## 3. Việc không nằm trong rule

- Số seller xin không bao giờ là số đề xuất. Số đề xuất là đầu ra CashFlow Engine.
- PD vào R10 (mục 4). Vượt `max_pd_bps` thì hồ sơ sang người, không tự từ chối. Bản prod dùng `LightGbmRiskModel` (`api/services/underwriting/risk.py`) bọc model `api/services/risk.py`. Đầu vào gồm hồ sơ shop trong snapshot (`archetype`, `shop_age_months`, `n_products`, `top3_share`, `rating`, `return_rate`), payout 12 tuần gần nhất (`payout_profile`, dùng chung với lúc train) và số của CashFlow Engine. Kết quả E6 có `pd_bps`, tối đa 5 yếu tố SHAP và lời công bố "PD ước lượng". Thiếu một trường hồ sơ shop thì không chấm, E6 ghi `missing_fields`, hồ sơ sang `ESCALATED` lý do `RISK_DATA_MISSING`. `RIVERBANK_WORKFLOW_RISK=baseline` chuyển về `BaselineRiskModel` 500 bps cố định, bộ golden dùng bản này để số không đổi theo model.
- Jev gate trước CIC: hỏi Jev (`typesafe/jev-1.14`) câu noul "đủ điều kiện tra CIC?" với state chỉ gồm số đã tính (gap, đề xuất, kỳ trích, payout, tuổi shop, hoàn hàng, rating, PD), không có định danh. Xác suất ≥ 0,6 mới gọi CIC. Dưới ngưỡng, lỗi mạng hoặc Jev trả sai định dạng thì không gọi CIC và không mặc định cho qua: E6a ghi `source = not_queried`, R06 fail thiếu dữ liệu, hồ sơ `ESCALATED` lý do `HARD_POLICY_FAILURE`. Người duyệt mở lại được bằng `POST /v1/cases/{id}/cic-inquiry` kèm lý do: hệ thống tra CIC bỏ qua Jev, ghi `override` vào E6a và chạy lại rule. Quyết định gate (`probability`, `threshold`, `eligible`, `model`, `error`) lưu trong E6a. Hồ sơ cần sao kê (R05 thiếu chứng từ) thì chưa hỏi Jev: E6a ghi `source = deferred_until_statement`, R06 và R07 thiếu dữ liệu không đẩy `ESCALATED`, hồ sơ vào `AWAITING_STATEMENT`. Nộp sao kê xong mới hỏi Jev, state có thêm kết quả đối chiếu sao kê (tiền sàn về, payout sàn, khớp hay không). Gate mở (`open`) không chờ vì không bao giờ chặn. Các lần chạy sau dùng lại E6a đã có, không hỏi Jev lại. PD thì chấm lại trên số tiền và kỳ hạn của lần chạy đó, vì hai số này là feature của model.
- Giới tính, tôn giáo, dân tộc, sức khỏe không có trong feature hay rule. Nhắc tới trong `note` thì chặn cả hồ sơ.
- Lãi và phí không có trong bản seller vì chưa có biểu phí ngân hàng.
- Tờ trình không viết điều khoản hợp đồng, khế ước, ủy nhiệm chi.

## 4. Bộ rule

Mỗi rule là hàm thuần nhận `PolicyInput`, trả `rule_id`, `hard`, `passed`, `reason`, `missing_data`, `evidence_id`. Rule không sửa số tiền, kỳ hạn hay kỳ trích.

`PolicyInput` lấy từ lần chạy engine: `amount` là số đề xuất (hoặc số người duyệt sửa), `weekly_deduction` là kỳ trích lớn nhất của lịch, `requested_amount = max(số seller xin, amount)`, `platform_supported_amount`, `shop_age_months`, `return_rate` từ E4, `cic_group` (nhóm nợ hiện tại xấu nhất trong các khoản), `cic_outstanding` (tổng dư nợ) từ E6a, `docs_verified` từ kết quả sao kê.

Thứ tự chạy: R09, R01, R04, R08, R02, R03, R05, R06, R07, R10.

### R01 Hạn mức sản phẩm

- Cứng. Đạt khi `amount <= max_amount_vnd` (300.000.000).
- Bằng chứng E5.

### R02 Tuổi shop

- Cứng. Đạt khi `shop_age_months >= min_shop_age_months` (6).
- Thiếu tuổi shop → fail, `missing_data`.
- Bằng chứng E4.

### R03 Tỷ lệ hoàn hàng

- **Mềm.** Đạt khi `return_rate <= max_return_rate` (0,15).
- Fail không đổi trạng thái. Người duyệt thấy cảnh báo trong kết quả rule.
- Bằng chứng E4.

### R04 Khung kỳ hạn

- Cứng. Đạt khi `min_tenor_weeks <= tenor_weeks <= max_tenor_weeks` (2 đến 26).
- Engine đã cộng tuần để kỳ trích xuống dưới trần trước khi rule này chạy. Rule chỉ fail khi engine chạm trần 26 tuần.
- Bằng chứng E5.

### R05 Sao kê khi xin vượt mức sàn

- Cứng. Ngưỡng `threshold = int(platform_supported_amount × 2,5)`.
- `requested_amount <= threshold` → đạt. **Bằng đúng mốc thì không đòi sao kê.**
- Vượt mốc và chưa có sao kê → fail, `missing_data`. Đây là trường hợp duy nhất đưa hồ sơ sang `AWAITING_STATEMENT`.
- Vượt mốc và đã có sao kê → đạt khi sao kê khớp, fail khi lệch. Lệch thì `ESCALATED`, lý do `HARD_POLICY_FAILURE`.
- Sao kê khớp khi `|payout sàn trong kỳ − tổng tiền sàn trên sao kê| ≤ 15% payout sàn` (`statement.mismatch_ratio`, trong code là `PolicyConfig.statement_mismatch_bps = 1500`). Lời văn: "không khớp; cần xác minh".
- Tổng tiền sàn trên sao kê đến từ người thẩm định nhập tay, hoặc từ file nộp qua `/statements/upload`: code bóc dòng, phân loại dòng tiền sàn, tự cộng các dòng trong kỳ. Model không cộng và không tính lệch.
- Bằng chứng E4, hoặc E8 khi đã có sao kê.

### R06 Nhóm nợ CIC

- Cứng. Đạt khi `cic_group <= max_cic_group` (2).
- Thiếu kết quả CIC → fail, `missing_data`.
- Chỉ chạy khi Gap > 0. Bằng chứng E6a.

### R07 Dư nợ CIC hiện hữu

- **Mềm.** Đạt khi `cic_outstanding <= payout_stress × 4 × 3,0`.
- Thiếu dữ liệu → fail mềm, `missing_data`.
- Bằng chứng E6a.

### R08 Trần trích payout

- Cứng. Đạt khi `weekly_deduction <= int(payout_stress × 0,25)`.
- Engine giãn kỳ hạn trước, không hạ số tiền để rule này đạt.
- Bằng chứng E5.

### R09 Trần cho vay điện tử

- Cứng. Áp cho người vay cá nhân (`borrower_is_individual`, bản demo luôn là cá nhân).
- Đạt khi `amount <= e_lending_cap_vnd` (100.000.000), theo Thông tư 06/2023/TT-NHNN.
- Pháp chế còn phải xác nhận cách áp khi đứng tên hộ kinh doanh.
- Bằng chứng E5.

### R10 Trần PD

- Cứng. Đạt khi `pd_bps <= max_pd_bps` (3.000, tức PD 30%).
- Ngưỡng chọn trên 5.000 shop sinh lại bằng `data/synth/train.py` seed 11, chấm bằng model đang chạy (tỷ lệ vỡ nợ của bộ này 20,7%, bộ test lúc train ghi 19,2%): trên 3.000 bps là 19% hồ sơ, tỷ lệ vỡ nợ 48% so với 14% ở nhóm còn lại, bắt được 45% số ca vỡ nợ. Dữ liệu là mô phỏng nên ngưỡng chỉ là ví dụ, ngân hàng chốt lại trên dữ liệu thật.
- Không chấm được PD thì R10 fail thiếu dữ liệu. Khi đó trạng thái là `RISK_DATA_MISSING`, trừ khi có rule cứng khác fail. Người duyệt chấp nhận được hồ sơ này khi có `verification_note`. R10 thiếu PD không chặn quyết định, nhưng vẫn ghi fail trong `policy_results_final`.
- Người duyệt hạ số tiền hoặc kỳ hạn bằng `ACCEPT_COUNTER` thì PD được chấm lại trên số mới.
- Bằng chứng E6.

## 5. Nhánh trạng thái sau rule

| Điều kiện, xét từ trên xuống | Trạng thái | `reason` |
|---|---|---|
| Gap = 0 | `NO_LOAN_NEEDED` | `null` |
| Rule cứng khác fail, hoặc sao kê lệch | `ESCALATED` | `HARD_POLICY_FAILURE` |
| Thiếu hồ sơ shop, không chấm được PD, chỉ R10 fail vì thiếu PD | `ESCALATED` | `RISK_DATA_MISSING` |
| Rule cứng duy nhất fail là R05 thiếu sao kê | `AWAITING_STATEMENT` | `STATEMENT_REQUIRED` |
| Tờ trình không qua kiểm tra sau 3 lần | `ESCALATED` | `MEMO_VALIDATION_FAILED` |
| Không rule cứng nào fail | `PENDING_REVIEW` | `null` |

`ESCALATED` chỉ sang `ACCEPTED` khi quyết định có `verification_note`. `NO_LOAN_NEEDED` không nhận quyết định chấp nhận.

## 6. Quyết định của người

Chỉ vai trò `underwriter`. Seller và tác nhân hệ thống bị từ chối.

| Quyết định | Hệ quả |
|---|---|
| `ACCEPT_PROPOSAL` | Chạy lại mười rule trên số đề xuất với bộ rule hiện tại. Bộ rule đổi từ lúc thẩm định → `409 POLICY_CHANGED_REASSESSMENT_REQUIRED`. Đạt thì `ACCEPTED` |
| `ACCEPT_COUNTER` | Người nhập số tiền và kỳ hạn 2 đến 26 tuần. Engine giãn kỳ hạn 2 tuần một lần đến khi kỳ trích không vượt trần. Tạo lần chạy mới `trigger = COUNTER`, chấm lại PD, chạy lại mười rule. Rule cứng fail → `409 HARD_POLICY_FAILURE` kèm mã rule, không lưu |
| `REJECT` | `REJECTED`. Gói bàn giao có `final_amount = null` |

Quyết định chấp nhận hết hiệu lực sau 14 ngày. Hết hạn thì `POST /handoff` trả `409 DECISION_EXPIRED_REASSESSMENT_REQUIRED`. Nhật ký `case_events` chỉ thêm, không sửa, không xóa.

## 7. Chưa có trong code

Các ý dưới đây có trong bản chính sách trước (ezXRiverbank) nhưng code chưa làm. Giữ ở đây để không mất ý, không coi là hành vi hiện tại.

- Cờ `is_fast_track` và lọc hàng đợi theo cờ.
- Tách đối chiếu sao kê thành rule riêng với lý do `R06_MISMATCH` và `R06_OFF_PLATFORM_NOT_EVIDENCE` (case D: tiền ngoài sàn không chứng minh khả năng trả).
- Bảng `loan_terms_versions` với `SYSTEM_PROPOSED`, `HUMAN_MODIFIED`, `SUPERSEDED`.
- Giám sát sau giải ngân: R08 trên payout tuần hiện tại, đề xuất giãn nợ D22, trạng thái `RESTRUCTURE_PROPOSED`.
- Che PII rồi lưu `sanitized_note` thay vì chặn cả hồ sơ.
