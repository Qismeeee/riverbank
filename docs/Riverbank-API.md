# Riverbank API

| | |
|---|---|
| Sản phẩm | Riverbank (ezXRiverbank) |
| Loại tài liệu | API Specification |
| Phiên bản | 0.2 |
| Bám theo | `Riverbank-BRD.md` 1.1, `Riverbank-SRS.md` 1.1, `Riverbank-TDD.md` 1.3 mục 7 |
| Nguồn hành vi | `api/routers/underwriting.py`, `api/services/underwriting/` |
| Nguồn ngưỡng | Code (`PolicyConfig`, `CashFlowConfig`, `Plan`), chép ở `policy/parameters.yaml` |

Mục 4 là API đang chạy trong `api/`, viết theo code `main` commit `68588f2`. Mục 6 là API trong kế hoạch, chưa có code. Mục 7 ghi chỗ đã chốt theo code và chỗ còn là kế hoạch.

---

## 1. Quy ước chung

### 1.1 Địa chỉ và định dạng

- Gốc: `/v1`. Riêng `/health` và `/` nằm ở gốc.
- Thân yêu cầu và phản hồi là JSON, UTF-8.
- Tiền là số nguyên VND. Không gửi số thập phân, không gửi chuỗi. Model dùng `strict=True`, nên `"150000000"` bị từ chối.
- Thời điểm là ISO 8601 có múi giờ, ví dụ `2026-10-23T08:00:00+00:00`. Ngày là `YYYY-MM-DD`.
- Tuần là số nguyên bắt đầu từ 0. Tuần 0 là tuần nhập hàng, chưa có payout của lô.
- `case_id` và `evidence_id` trên đường dẫn là UUID. Sai mẫu thì `422`.
- Mọi trường thừa trong thân yêu cầu bị từ chối (`extra="forbid"`).

### 1.2 Danh tính và vai trò

Bản demo đọc danh tính từ header. Header này chỉ có hiệu lực khi `RIVERBANK_ENV=local` và `RIVERBANK_WORKFLOW_MODE=demo`. Ngoài điều kiện đó mọi đầu `/v1` trả `503 VERIFIED_IDENTITY_ADAPTER_REQUIRED` hoặc `503 WORKFLOW_DEMO_DISABLED`.

| Header | Giá trị |
|---|---|
| `X-Actor-ID` | Định danh người gọi. Với seller phải bằng `seller_id` của hồ sơ |
| `X-Actor-Role` | `seller` hoặc `underwriter` |

| Vai trò | Được gọi |
|---|---|
| `seller` | Tạo hồ sơ của chính mình, đọc `seller-view` của chính mình |
| `underwriter` | Hàng đợi, chi tiết hồ sơ, bằng chứng, nhập sao kê, quyết định, bàn giao |

Bản thật thay header bằng adapter danh tính của ngân hàng. Không có vai trò hệ thống nào được gọi đầu quyết định (SRS FR-31).

### 1.3 Chống ghi trùng và ghi đè

- **`Idempotency-Key`** (1–128 ký tự) bắt buộc ở `POST /v1/cases` và `POST /v1/cases/{case_id}/decisions`. Cùng khóa, cùng nội dung thì trả lại kết quả cũ. Cùng khóa, khác nội dung thì `409 IDEMPOTENCY_KEY_REUSED`.
- **`expected_version`** bắt buộc ở các lệnh đổi hồ sơ. Giá trị phải bằng `version` hiện tại của hồ sơ, nếu không thì `409 CASE_VERSION_CONFLICT`. Mỗi lần ghi thành công tăng `version` thêm 1. Bàn giao LOS ghi hai lần (bắt đầu gửi, kết quả gửi) nên tăng 2.

`version` ở đây là phiên bản bản ghi hồ sơ, dùng để khóa lạc quan. Nó không phải phiên bản điều khoản vay `loan_terms_versions` trong SRS. Xem mục 7.

### 1.4 Lỗi

Lỗi nghiệp vụ:

```json
{ "detail": { "code": "HARD_POLICY_FAILURE", "details": ["R08"] } }
```

Lỗi sai kiểu dữ liệu do FastAPI trả, mã `422`:

```json
{ "detail": [ { "type": "...", "loc": ["body", "sell_weeks"], "msg": "...", "input": 40 } ] }
```

| HTTP | Khi nào |
|---|---|
| `401` | Thiếu `X-Actor-ID` |
| `403` | Sai vai trò, hoặc seller đụng hồ sơ không phải của mình |
| `404` | Không có hồ sơ hoặc bằng chứng |
| `409` | Trạng thái hồ sơ không cho phép thao tác, xung đột `version`, khóa idempotency bị dùng lại, rule cứng fail |
| `422` | Đầu vào sai, bị chặn ở cổng nhập, thiếu consent, thiếu dữ liệu payout |
| `503` | Chế độ demo chưa bật, thiếu database |

Bảng mã lỗi đầy đủ ở mục 5.

---

## 2. Trạng thái hồ sơ

Mã trạng thái theo `knowledge/glossary.md` và SRS mục 7.

| Mã | Ai đặt | Ghi chú |
|---|---|---|
| `NO_LOAN_NEEDED` | Hệ thống | Gap = 0. Không gọi CIC, không chấm PD, không chạy rule |
| `AWAITING_STATEMENT` | Hệ thống | Rule cứng duy nhất còn thiếu là chứng từ R05 |
| `PENDING_REVIEW` | Hệ thống | Mọi rule đạt |
| `ESCALATED` | Hệ thống | Rule cứng fail, sao kê lệch, hoặc tờ trình không qua kiểm tra lần thứ 3 |
| `ACCEPTED` | Người ngân hàng | Chỉ qua `POST /decisions` |
| `REJECTED` | Người ngân hàng | Chỉ qua `POST /decisions` |

`BLOCKED_INPUT` có trong enum nhưng bản hiện tại không lưu hồ sơ bị chặn. Đầu tạo hồ sơ trả `422 BLOCKED_INPUT` và không sinh `case_id`.

Không có đường từ trạng thái phân tích sang `ACCEPTED` hoặc `REJECTED` (SRS FR-29).

```text
POST /cases ──> NO_LOAN_NEEDED
            ├─> PENDING_REVIEW ─────────────┐
            ├─> ESCALATED ──────────────────┤
            └─> AWAITING_STATEMENT          │
                   │ POST /statements       │
                   ├─> PENDING_REVIEW ──────┤
                   └─> ESCALATED ───────────┤
                                            │ POST /decisions (người ngân hàng)
                                            ├─> ACCEPTED ─┐
                                            └─> REJECTED ─┤ POST /handoff
                                                          └─> handoff.status = SUCCEEDED | FAILED
```

---

## 3. Danh sách đầu API

| Phương thức | Đường dẫn | Vai trò | Tình trạng | Bám |
|---|---|---|---|---|
| `GET` | `/health` | Không cần | Có | Vận hành |
| `GET` | `/` | Không cần | Có | Vận hành |
| `POST` | `/v1/cases` | seller | Có | FR-01..FR-28 |
| `GET` | `/v1/cases/{case_id}/seller-view` | seller | Có | FR-27, FR-28 |
| `GET` | `/v1/queue` | underwriter | Có | FR-29 |
| `GET` | `/v1/cases/{case_id}` | underwriter | Có | FR-30 |
| `GET` | `/v1/cases/{case_id}/evidence/{evidence_id}` | underwriter | Có | FR-07, FR-30 |
| `POST` | `/v1/cases/{case_id}/statements` | underwriter | Có | FR-21..FR-23 |
| `POST` | `/v1/cases/{case_id}/decisions` | underwriter | Có | FR-31..FR-34 |
| `POST` | `/v1/cases/{case_id}/handoff` | underwriter | Có | FR-35, FR-36 |
| `POST` | `/v1/copilot/extract-plan` | seller | Chưa có | FR-02, FR-03 |
| `POST` | `/v1/cases/{case_id}/copilot/ask` | seller | Chưa có | FR-27 |
| `POST` | `/v1/cases/{case_id}/statements/presign` | seller | Chưa có | FR-22 |
| `POST` | `/v1/cases/{case_id}/statements/confirm` | seller | Chưa có | FR-22 |
| `POST` | `/v1/webhooks/los-status` | LOS ngân hàng | Chưa có | FR-36 |
| `POST` | `/v1/cases/{case_id}/simulate-p7-event` | underwriter (demo) | Chưa có | FR-37 |
| `POST` | `/v1/cases/{case_id}/restructure-decision` | underwriter | Chưa có | FR-38 |
| `GET` | `/v1/studio/policy` | underwriter | Chưa có | FR-18 |
| `GET` | `/v1/studio/eval-metrics` | underwriter | Chưa có | NFR-01..NFR-06 |

---

## 4. API đang chạy

### 4.1 `GET /health`

Kiểm tra sống. Không cần danh tính.

```json
{
  "status": "ok",
  "env": "local",
  "db": "ok",
  "s3": "skipped",
  "host": "riverbank-api-1",
  "time": "2026-10-23T08:00:00+00:00"
}
```

`db` và `s3` là `ok`, `skipped` (chưa cấu hình), hoặc `error: <Tên lỗi>`. Có một mục `error` thì `status` là `degraded`.

### 4.2 `GET /`

```json
{ "service": "riverbank-api", "docs": "/docs" }
```

OpenAPI sinh tự động ở `/docs` và `/openapi.json`.

---

### 4.3 `POST /v1/cases`, Tạo hồ sơ và thẩm định

Seller gửi kế hoạch một lô hàng. Hệ thống chạy hết nhánh thẩm định trong cùng yêu cầu và trả trạng thái cuối của bước phân tích.

**Header:** `X-Actor-ID` = `seller_id`, `X-Actor-Role: seller`, `Idempotency-Key`.

**Thân yêu cầu** (`Plan`):

| Trường | Kiểu | Ràng buộc |
|---|---|---|
| `seller_id` | string | 1–100 ký tự. Phải bằng `X-Actor-ID` |
| `shop_id` | string | 1–100 ký tự. Phải thuộc seller |
| `lot_value` | int | > 0 |
| `cash_on_hand` | int | ≥ 0 |
| `supplier_schedule` | `[{week, amount}]` | 1–100 dòng. `week` 0–52 và không vượt `sell_weeks`. `amount` > 0. Tổng `amount` phải bằng `lot_value` |
| `sell_weeks` | int | 1–26 |
| `requested_amount` | int | > 0, ≤ 300.000.000 |
| `note` | string | ≤ 2000 ký tự, mặc định rỗng |

```json
{
  "seller_id": "demo-b",
  "shop_id": "demo-b",
  "lot_value": 200000000,
  "cash_on_hand": 120000000,
  "supplier_schedule": [{ "week": 0, "amount": 200000000 }],
  "sell_weeks": 6,
  "requested_amount": 150000000,
  "note": ""
}
```

**Thứ tự xử lý:**

1. Quét `note`. Có lệnh bỏ qua chính sách hoặc ép duyệt, có SĐT, CCCD, số tài khoản, hoặc nhắc thuộc tính nhạy cảm thì trả `422 BLOCKED_INPUT`. Không tạo hồ sơ.
2. Kiểm tra khóa idempotency.
3. Đọc consent và feature của đúng `seller_id` + `shop_id`. Consent phải có `reference`, mục đích `WORKING_CAPITAL_UNDERWRITING`, chưa thu hồi, còn hạn. Thiếu thì `422 CONSENT_REQUIRED`.
4. Kiểm tra snapshot feature: đúng seller, có nguồn gốc payout (`observed` hoặc `simulated`), 4–104 tuần payout liên tục, mỗi tuần bắt đầu thứ Hai và đã kết thúc.
5. Chạy CashFlow Engine. Lưu bằng chứng `E4` (feature) và `E5` (dòng tiền).
6. Gap = 0: trạng thái `NO_LOAN_NEEDED`. Dừng ở đây, không gọi CIC.
7. Gap > 0: chấm PD (`E6`), tra CIC (`E6a`), chạy Policy Engine (`E7`).
   - Có hard fail khác R05 thiếu chứng từ, hoặc sao kê lệch: `ESCALATED`, lý do `HARD_POLICY_FAILURE`.
   - Snapshot thiếu hồ sơ shop nên không chấm được PD: `ESCALATED`, lý do `RISK_DATA_MISSING`. E6 ghi `missing_fields`.
   - Hard fail duy nhất là R05 thiếu chứng từ: `AWAITING_STATEMENT`.
   - Còn lại: `PENDING_REVIEW`.
8. Sinh tờ trình và bản seller từ template, kiểm từng câu với bằng chứng. Thử tối đa 3 lần. Không qua thì `ESCALATED`, lý do `MEMO_VALIDATION_FAILED`.

**Phản hồi `201`** (`CaseSummary`):

```json
{
  "case_id": "6f1d2c1e-6a3b-4a51-9d2a-1b7c2f0e9a11",
  "seller_id": "demo-b",
  "shop_id": "demo-b",
  "status": "PENDING_REVIEW",
  "version": 1,
  "reason": null,
  "created_at": "2026-10-23T08:00:00+00:00",
  "demo": true
}
```

`reason` là `null`, `STATEMENT_REQUIRED`, `HARD_POLICY_FAILURE`, `RISK_DATA_MISSING`, hoặc `MEMO_VALIDATION_FAILED`.

**Lỗi:** `403 SELLER_SCOPE_MISMATCH`, `403 SELLER_SHOP_SCOPE_NOT_FOUND`, `403 FEATURE_SCOPE_MISMATCH`, `403 CIC_SCOPE_MISMATCH`, `409 IDEMPOTENCY_KEY_REUSED`, `422 BLOCKED_INPUT`, `422 CONSENT_REQUIRED`, `422 PAYOUT_HISTORY_REQUIRED`, `422 INVALID_PAYOUT_HISTORY`, và các lỗi snapshot ở mục 5.

---

### 4.4 `GET /v1/cases/{case_id}/seller-view`, Bản cho seller

**Header:** `X-Actor-Role: seller`, `X-Actor-ID` = chủ hồ sơ.

**Phản hồi `200`** (`SellerView`): toàn bộ trường của `CaseSummary`, thêm:

| Trường | Ghi chú |
|---|---|
| `explanation` | Danh sách câu của bản seller (D10). Rỗng khi bản mới nhất chưa qua kiểm tra |
| `decision` | `null` khi chưa có quyết định. Có thì gồm `decision_type`, `final_amount`, `final_tenor_weeks` |

Mỗi câu (`MemoSentence`):

```json
{
  "template": "proposal",
  "text": "Đề xuất ... VND trong ... tuần.",
  "claims": { "amount_proposed": 0, "tenor_weeks": 0 },
  "evidence_id": "..."
}
```

Số trong `text` lấy từ `claims`, và `claims` lấy nguyên từ payload bằng chứng. Bản seller không có câu PD, CIC, hay kết quả rule.

**Lỗi:** `403 SELLER_SCOPE_MISMATCH`, `404 CASE_NOT_FOUND`.

---

### 4.5 `GET /v1/queue`, Hàng đợi

**Header:** `X-Actor-Role: underwriter`.

| Query | Kiểu | Mặc định |
|---|---|---|
| `status` | Một mã trạng thái | Không lọc |
| `limit` | 1–100 | 50 |
| `offset` | ≥ 0 | 0 |

Hàng đợi chỉ chứa `PENDING_REVIEW`, `ESCALATED`, `NO_LOAN_NEEDED`, `AWAITING_STATEMENT`, sắp theo `created_at` tăng dần.

```json
{ "items": [ { "case_id": "...", "status": "ESCALATED", "version": 1, "...": "..." } ], "limit": 50, "offset": 0 }
```

---

### 4.6 `GET /v1/cases/{case_id}`, Chi tiết hồ sơ

**Header:** `X-Actor-Role: underwriter`.

**Phản hồi `200`** (`CaseDetail`): `CaseSummary` thêm các trường:

| Trường | Nội dung |
|---|---|
| `plan` | Kế hoạch seller đã gửi |
| `consent` | Bản consent dùng lúc thẩm định |
| `runs` | Mỗi lần chạy thẩm định là một phần tử, xem dưới |
| `evidence` | Mọi bằng chứng của hồ sơ |
| `documents` | Mọi lần sinh tờ trình, kể cả lần chưa qua |
| `events` | Nhật ký chỉ thêm |
| `decision` | Quyết định của người, hoặc `null` |
| `handoff` | Gói bàn giao LOS, hoặc `null` |

Một phần tử `runs`:

| Trường | Nội dung |
|---|---|
| `run_id`, `run_no` | Định danh và số thứ tự lần chạy |
| `trigger` | `INITIAL`, `STATEMENT`, hoặc `COUNTER` |
| `input_snapshot`, `input_hash` | Plan, feature, consent đã dùng và mã băm |
| `engine_version`, `config` | Phiên bản và cấu hình CashFlow Engine |
| `policy_version`, `policy_hash` | Bộ rule đã chạy |
| `cashflow` | `payout_base`, `payout_stress`, `weeks`, `stress_weeks`, `gap`, `negative_weeks`, `amount_proposed`, `tenor_weeks`, `weekly_deduction`, `weekly_deductions`, `tenor_trace`, `payout_warning_threshold` |
| `risk` | `pd_bps` (`null` khi thiếu dữ liệu), `model_version`, `source` (`lightgbm` hoặc `demo`), `factors[]` (`name`, `direction`, `contribution`, `value`), `missing_fields[]`, `disclosure`. `null` khi Gap = 0 |
| `cic` | `bureau_group`, `outstanding`, `queried_at`. `null` khi Gap = 0 |
| `policy` | `results[]` (`rule_id`, `hard`, `passed`, `reason`, `missing_data`, `evidence_id`), `hard_fail`, `summary` |
| `policy_input` | Snapshot đưa vào Policy Engine |
| `statement` | Kết quả đối chiếu sao kê, hoặc `null` |

Một phần tử `events`:

```json
{
  "event_id": "...",
  "actor": "ORCHESTRATOR",
  "event_type": "STATE_CHANGED",
  "previous_status": "ANALYZING",
  "next_status": "PENDING_REVIEW",
  "reason": "ASSESSMENT_COMPLETED",
  "occurred_at": "...",
  "evidence_ids": ["..."]
}
```

`event_type` là `STATE_CHANGED`, `STATEMENT_RECORDED`, `DECISION_RECORDED`, `HANDOFF_ATTEMPT`, `HANDOFF_RESULT`. Không có đầu API nào sửa hay xóa nhật ký (FR-38, FR-39).

---

### 4.7 `GET /v1/cases/{case_id}/evidence/{evidence_id}`, Mở bằng chứng

**Header:** `X-Actor-Role: underwriter`.

**Phản hồi `200`** (`EvidenceResponse`):

```json
{
  "evidence_id": "...",
  "case_id": "...",
  "run_id": "...",
  "code": "E5",
  "schema_version": "1",
  "payload": { "gap": 0, "...": "..." },
  "payload_hash": "sha256 hex",
  "created_at": "..."
}
```

Server băm lại `payload` mỗi lần đọc. Lệch mã băm thì `409 EVIDENCE_HASH_MISMATCH`.

| `code` | Nội dung | Có khi |
|---|---|---|
| `E4` | Snapshot feature: payout tuần, tuổi shop, tỷ lệ hoàn, mức dữ liệu sàn hỗ trợ, nguồn dữ liệu | Mọi lần chạy |
| `E5` | Dòng tiền, Gap, đề xuất, lịch trích | Mọi lần chạy |
| `E6` | PD và yếu tố giải thích | Gap > 0 |
| `E6a` | Kết quả CIC qua cổng ngân hàng (D04) | Gap > 0 |
| `E7` | Kết quả từng rule | Gap > 0 |
| `E8` | Bảng đối chiếu sao kê (D08) | Đã nhập sao kê |

**Lỗi:** `404 CASE_NOT_FOUND`, `404 EVIDENCE_NOT_FOUND`, `409 EVIDENCE_HASH_MISMATCH`.

---

### 4.8 `POST /v1/cases/{case_id}/statements`, Nhập tổng sao kê

Bản hiện tại để người thẩm định nhập tổng tiền sàn trên sao kê. Đầu upload file của seller ở mục 6.3.

**Header:** `X-Actor-Role: underwriter`.

**Điều kiện:** hồ sơ ở `AWAITING_STATEMENT` hoặc `ESCALATED`, và lần chạy trước có R05 fail. Không thỏa thì `409 STATEMENT_NOT_REQUESTED`.

**Thân yêu cầu** (`Statement`):

| Trường | Kiểu | Ràng buộc |
|---|---|---|
| `expected_version` | int | ≥ 1 |
| `period_start` | date | Thứ Hai |
| `period_end` | date | Thứ Hai, sau `period_start`. Khoảng `[start, end)` phải trùng trọn các tuần payout trong snapshot |
| `marketplace_total` | int | Tổng dòng tiền từ sàn trên sao kê, ≥ 0 |
| `document_ref` | string | 1–500 ký tự. Tham chiếu file gốc |

**Cách đối chiếu:** `platform_total` là tổng payout sàn các tuần trong khoảng. Lệch là `|platform_total − marketplace_total|`. Khớp khi lệch ≤ 15% `platform_total` (`statement_mismatch_ratio`). Hệ thống chạy lại thẩm định với kết quả này (`trigger = STATEMENT`), giữ nguyên PD và CIC của lần đầu.

- Khớp: R05 đạt, hồ sơ sang `PENDING_REVIEW` nếu không còn rule cứng nào fail.
- Lệch: `ESCALATED`. Thông điệp là "không khớp; cần xác minh". Không dùng từ trong `knowledge/guardrails.md`.

**Phản hồi `200`:** `CaseSummary`.

**Lỗi:** `409 STATEMENT_NOT_REQUESTED`, `409 CASE_VERSION_CONFLICT`, `422 INVALID_STATEMENT_PERIOD`, `422 STATEMENT_PERIOD_NOT_COVERED`.

---

### 4.9 `POST /v1/cases/{case_id}/decisions`, Quyết định của người ngân hàng

Đầu duy nhất đưa hồ sơ sang `ACCEPTED` hoặc `REJECTED`.

**Header:** `X-Actor-Role: underwriter`, `Idempotency-Key`.

**Thân yêu cầu** (`DecisionInput`):

| Trường | Kiểu | Ràng buộc |
|---|---|---|
| `expected_version` | int | ≥ 1 |
| `decision_type` | enum | `ACCEPT_PROPOSAL`, `ACCEPT_COUNTER`, `REJECT` |
| `final_amount` | int | Chỉ với `ACCEPT_COUNTER`, bắt buộc, > 0 |
| `final_tenor_weeks` | int | Chỉ với `ACCEPT_COUNTER`, bắt buộc, 2–26 |
| `verification_note` | string | ≤ 2000. Bắt buộc khác rỗng khi chấp nhận hồ sơ `ESCALATED` |
| `reason` | string | 1–2000, bắt buộc |

```json
{
  "expected_version": 1,
  "decision_type": "ACCEPT_COUNTER",
  "final_amount": 80000000,
  "final_tenor_weeks": 8,
  "verification_note": "",
  "reason": "Giảm số theo khẩu vị chi nhánh"
}
```

**Kiểm tra trước khi ghi:**

1. Hồ sơ ở `PENDING_REVIEW` hoặc `ESCALATED` và chưa có quyết định. Không thì `409 DECISION_NOT_ALLOWED`.
2. Với quyết định chấp nhận:
   - Gap = 0 thì `409 NO_LOAN_NEEDED` (FR-34).
   - Hồ sơ `ESCALATED` thiếu `verification_note` thì `409 VERIFICATION_NOTE_REQUIRED` (FR-33).
   - `ACCEPT_PROPOSAL` mà bộ rule đã đổi từ lúc thẩm định thì `409 POLICY_CHANGED_REASSESSMENT_REQUIRED`.
   - `ACCEPT_COUNTER` chạy lại engine với số mới (`trigger = COUNTER`). Engine kéo dài kỳ hạn 2 tuần một lần cho đến khi kỳ trích không vượt 25% payout xấu. Không xếp được lịch thì `409 COUNTER_TERMS_INVALID`.
   - Chạy lại toàn bộ rule trên bộ số cuối. Có hard fail thì `409 HARD_POLICY_FAILURE`, `details` là danh sách mã rule (FR-32).
   - Sao kê đã nhập mà lệch thì `409 STATEMENT_MISMATCH`.
   - Tờ trình mới nhất phải đã qua kiểm tra và thuộc đúng lần chạy cuối, không thì `409 MEMO_NOT_VALIDATED`.
3. `REJECT` không cần các kiểm tra ở bước 2.

**Phản hồi `200`** (`DecisionResponse`): `CaseSummary` thêm `decision`:

| Trường | Nội dung |
|---|---|
| `decision_id`, `run_id`, `document_id` | Lần chạy và tờ trình làm căn cứ |
| `decision_type`, `actor`, `reason`, `verification_note` | Người quyết định và lý do |
| `final_amount`, `final_tenor_weeks`, `weekly_deductions` | Số chốt. `null` và `[]` khi `REJECT` |
| `policy_results_final` | Kết quả rule trên số chốt. `null` khi `REJECT` |
| `policy_hash` | Bộ rule đã chạy |
| `decided_at` | Thời điểm |
| `expires_at` | `decided_at` + 14 ngày khi chấp nhận (FR-31b). `null` khi từ chối |
| `idempotency_key`, `request_hash` | Chống ghi trùng |

Ghi quyết định thành công thì hệ thống tạo `handoff` ở trạng thái `PENDING`. Chưa gửi LOS.

---

### 4.10 `POST /v1/cases/{case_id}/handoff`, Bàn giao LOS

**Header:** `X-Actor-Role: underwriter`.

**Thân yêu cầu:** `{ "expected_version": 2 }`.

**Điều kiện:** hồ sơ `ACCEPTED` hoặc `REJECTED` và đã có gói bàn giao. Không thì `409 HUMAN_DECISION_REQUIRED`. Quyết định chấp nhận quá `expires_at` thì `409 DECISION_EXPIRED_REASSESSMENT_REQUIRED`.

**Cách gửi:**

1. Đặt `status = SENDING`, tăng `attempts`, giữ khóa 60 giây. Đang có khóa thì `409 HANDOFF_IN_PROGRESS`.
2. Băm lại gói. Lệch thì `409 HANDOFF_HASH_MISMATCH`.
3. Gửi LOS với `idempotency_key` cố định của gói. Bản demo dùng `MockLosAdapter`, trả `MOCK-<key>`.
4. Ghi `SUCCEEDED` kèm `los_reference`, hoặc `FAILED` kèm `last_error` (`LOS_UNAVAILABLE`, `LOS_INVALID_RESPONSE`). Quyết định của người không bị đổi khi gửi lỗi (FR-36). Gọi lại đầu này để thử lại.

Đã `SUCCEEDED` thì gọi lại trả kết quả cũ, không gửi lần hai.

**Gói gửi LOS** (`handoff.payload`): `case_id`, `seller_id`, `shop_id`, `decision`, `risk`, `cic`, `policy`, `document` (tờ trình đã qua), `evidence_ids`, `demo`. Gói `REJECT` có `final_amount = null` (FR-35).

**Phản hồi `200`** (`HandoffResponse`): `CaseSummary` thêm `handoff` không kèm `payload`:

```json
{
  "idempotency_key": "...",
  "status": "SUCCEEDED",
  "payload_hash": "...",
  "attempts": 1,
  "last_error": null,
  "los_reference": "MOCK-...",
  "lease_until": null
}
```

Riverbank không gửi lệnh giải ngân, hạch toán, hay trích nợ sang core (FR-37 bản Riverbank).

---

## 5. Mã lỗi

| Mã | HTTP | Đầu API | Nghĩa |
|---|---|---|---|
| `AUTHENTICATION_REQUIRED` | 401 | `/v1/*` | Thiếu `X-Actor-ID` |
| `ROLE_NOT_ALLOWED` | 403 | `/v1/*` | `X-Actor-Role` không phải `seller` hay `underwriter` |
| `UNDERWRITER_REQUIRED` | 403 | Đầu của ngân hàng | Seller gọi đầu của ngân hàng |
| `SELLER_SCOPE_MISMATCH` | 403 | Tạo hồ sơ, seller-view | Seller đụng hồ sơ hoặc `seller_id` không phải của mình |
| `SELLER_SHOP_SCOPE_NOT_FOUND` | 403 | Tạo hồ sơ | Shop không thuộc seller |
| `FEATURE_SCOPE_MISMATCH` | 403 | Tạo hồ sơ | Snapshot feature không đúng seller hoặc shop |
| `CIC_SCOPE_MISMATCH` | 403 | Tạo hồ sơ | Kết quả CIC trả về sai seller |
| `CASE_NOT_FOUND` | 404 | Mọi đầu có `case_id` | Không có hồ sơ |
| `EVIDENCE_NOT_FOUND` | 404 | Bằng chứng | Không có bằng chứng trong hồ sơ |
| `IDEMPOTENCY_KEY_REUSED` | 409 | Tạo hồ sơ, quyết định | Khóa cũ, nội dung khác |
| `CASE_VERSION_CONFLICT` | 409 | Lệnh ghi | `expected_version` cũ |
| `STATEMENT_NOT_REQUESTED` | 409 | Sao kê | Hồ sơ không đang chờ sao kê |
| `DECISION_NOT_ALLOWED` | 409 | Quyết định | Sai trạng thái hoặc đã có quyết định |
| `NO_LOAN_NEEDED` | 409 | Quyết định | Chấp nhận hồ sơ Gap = 0 |
| `VERIFICATION_NOTE_REQUIRED` | 409 | Quyết định | Chấp nhận hồ sơ `ESCALATED` thiếu ghi chú xác minh |
| `POLICY_CHANGED_REASSESSMENT_REQUIRED` | 409 | Quyết định | Bộ rule đã đổi sau thẩm định |
| `COUNTER_TERMS_INVALID` | 409 | Quyết định | Không xếp được lịch cho số chốt |
| `HARD_POLICY_FAILURE` | 409 | Quyết định | Số chốt làm fail rule cứng |
| `STATEMENT_MISMATCH` | 409 | Quyết định | Chấp nhận khi sao kê lệch |
| `MEMO_NOT_VALIDATED` | 409 | Quyết định | Tờ trình chưa qua kiểm tra |
| `MEMO_EVIDENCE_MISMATCH` | 409 | Quyết định | Câu trong tờ trình lệch bằng chứng |
| `EVIDENCE_HASH_MISMATCH` | 409 | Bằng chứng, quyết định | Bằng chứng bị đổi sau khi lưu |
| `HUMAN_DECISION_REQUIRED` | 409 | Bàn giao | Chưa có quyết định |
| `DECISION_EXPIRED_REASSESSMENT_REQUIRED` | 409 | Bàn giao | Quyết định chấp nhận quá 14 ngày |
| `HANDOFF_IN_PROGRESS` | 409 | Bàn giao | Đang có lần gửi khác |
| `HANDOFF_HASH_MISMATCH` | 409 | Bàn giao | Gói bị đổi sau khi tạo |
| `FEATURE_HASH_MISMATCH` | 409 | Tạo hồ sơ | Snapshot feature trong Postgres lệch mã băm |
| `BLOCKED_INPUT` | 422 | Tạo hồ sơ | Ghi chú có lệnh ép duyệt, PII, hoặc thuộc tính nhạy cảm |
| `CONSENT_REQUIRED` | 422 | Tạo hồ sơ | Không có consent hiệu lực |
| `PAYOUT_HISTORY_REQUIRED` | 422 | Tạo hồ sơ | Dưới 4 hoặc trên 104 tuần payout |
| `INVALID_PAYOUT_PERIOD` | 422 | Tạo hồ sơ | `week_start` sai định dạng |
| `INVALID_PAYOUT_HISTORY` | 422 | Tạo hồ sơ | Tuần không liên tục, chưa kết thúc, hoặc số âm |
| `UNAPPROVED_FEATURE_FIELDS` | 422 | Tạo hồ sơ | Snapshot có trường ngoài danh sách cho phép |
| `PAYOUT_PROVENANCE_REQUIRED` | 422 | Tạo hồ sơ | Thiếu nguồn gốc payout |
| `SIMULATION_MUST_BE_LABELLED` | 422 | Tạo hồ sơ | Dữ liệu synthetic không gắn nhãn mô phỏng |
| `INVALID_FEATURE_SNAPSHOT` | 422 | Tạo hồ sơ | Thiếu mức dữ liệu sàn hỗ trợ hoặc lời công bố nguồn, hoặc hồ sơ shop sai kiểu, ngoài khoảng |
| `UNSUPPORTED_SHOPEE_SOURCE` | 422 | Tạo hồ sơ | Nguồn Shopee chưa hỗ trợ |
| `INVALID_RISK_RESULT` | 422 | Tạo hồ sơ | PD ngoài khoảng, thiếu yếu tố, hoặc báo thiếu dữ liệu mà vẫn có PD |
| `INVALID_STATEMENT_PERIOD` | 422 | Sao kê | Ngày sai định dạng |
| `STATEMENT_PERIOD_NOT_COVERED` | 422 | Sao kê | Kỳ không trùng trọn các tuần payout |
| `VERIFIED_IDENTITY_ADAPTER_REQUIRED` | 503 | `/v1/*` | Chưa có adapter danh tính ngoài chế độ demo local |
| `WORKFLOW_DEMO_DISABLED` | 503 | `/v1/*` | Chưa bật chế độ demo |
| `INVALID_WORKFLOW_STORAGE`, `INVALID_WORKFLOW_FEATURES`, `INVALID_WORKFLOW_RISK` | 503 | `/v1/*` | Cấu hình sai |
| `DATABASE_REQUIRED` | 503 | `/v1/*` | Chọn Postgres nhưng thiếu `DATABASE_URL` |

---

## 6. API trong kế hoạch

Các đầu dưới đây là kế hoạch, chưa có code. Hợp đồng dưới đây theo quy ước mục 1. Đầu nào có gọi model phải đi đúng chỗ ở bảng "Chỗ nào được gọi model" trong `KNOWLEDGE_BASE.md`.

### 6.1 `POST /v1/copilot/extract-plan`

Seller chat tự do, nhận lại năm trường để xác nhận trên thẻ. Không tạo hồ sơ (FR-02).

```json
{ "seller_id": "demo-b", "shop_id": "demo-b", "message": "Mình nhập lô 200tr, có 120tr, trả hết tuần 0, bán 6 tuần, xin 150tr" }
```

```json
{
  "draft": {
    "lot_value": 200000000,
    "cash_on_hand": 120000000,
    "supplier_schedule": [{ "week": 0, "amount": 200000000 }],
    "sell_weeks": 6,
    "requested_amount": 150000000
  },
  "missing_fields": [],
  "sanitized_message": "..."
}
```

- Che SĐT, CCCD, số tài khoản trên `message` trước khi gọi Jev hoặc OpenRouter (FR-03).
- Jev `jev-input-guard` trên chữ đã che. Xác suất ≥ `guardrail.block_probability` thì `422 BLOCKED_INPUT`.
- Prompt `copilot-extract` chỉ trích đúng số seller nói. Không tính Gap, không đề xuất số.
- Seller bấm xác nhận thì client gọi `POST /v1/cases` với `draft`.

### 6.2 `POST /v1/cases/{case_id}/copilot/ask`

Seller hỏi lại trên số đã khóa của hồ sơ.

```json
{ "question": "Sao chỉ được vay ít hơn số mình xin?" }
```

```json
{ "answer": [ { "text": "...", "evidence_id": "..." } ] }
```

- Prompt `copilot-qa`. Đầu vào chỉ là payload bằng chứng của lần chạy cuối.
- Câu trả lời đi qua cùng bộ kiểm tra số và từ cấm như tờ trình. Không nêu lãi và phí khi chưa có biểu phí ngân hàng (FR-27).

### 6.3 `POST /v1/cases/{case_id}/statements/presign` và `/statements/confirm`

Seller nộp file sao kê. Thay cho đầu nhập tổng ở mục 4.8 khi làm xong.

`presign` (hồ sơ phải ở `AWAITING_STATEMENT`):

```json
{ "file_name": "saoke-thang9.pdf", "content_type": "application/pdf" }
```

```json
{ "upload_url": "https://...", "object_key": "statements/<case_id>/<uuid>.pdf", "expires_at": "..." }
```

`confirm`:

```json
{ "expected_version": 1, "object_key": "statements/<case_id>/<uuid>.pdf", "period_start": "2026-08-03", "period_end": "2026-10-26" }
```

- CSV đi parser Python. PDF và ảnh đi `statement-ocr`, chỉ bóc dòng `{date, raw_desc, credit_amount}`.
- Jev `jev-line-classifier` gắn từng dòng có phải payout sàn. Python cộng tổng, không để model cộng.
- So như R05 hiện tại: lệch trên 15% thì R05 fail và `ESCALATED`. Kế hoạch thêm lý do riêng khi phần xin vượt hạn mức chỉ dựa vào dòng ngoài sàn (`policy/policy.md` mục 7). Cả hai sang `ESCALATED`.
- Lưu bảng đối chiếu thành `E8`. Phản hồi `CaseSummary`.

### 6.4 `POST /v1/webhooks/los-status`

LOS ngân hàng báo trạng thái sau bàn giao.

```json
{
  "case_id": "...",
  "idempotency_key": "...",
  "los_ref": "LOS-2026-0001",
  "contract_status": "STANDARD_ECONTRACT_SIGNED",
  "disbursement_status": "DISBURSED_TO_SUPPLIER",
  "occurred_at": "..."
}
```

- Xác thực bằng chữ ký do ngân hàng cấp, không dùng header vai trò. Cơ chế ký cần chốt với ngân hàng.
- `idempotency_key` phải bằng khóa của gói đã gửi. Cùng sự kiện gửi lại thì trả `200` không ghi thêm.
- Chỉ ghi trạng thái hợp đồng và giải ngân vào `handoff` và `case_events`. Không đổi quyết định, không đổi số tiền.

### 6.5 `POST /v1/cases/{case_id}/simulate-p7-event`

Chỉ bật ở demo. Giả lập payout tuần tụt để chạy Monitor.

```json
{ "expected_version": 4, "current_week_payout": 42000000, "return_rate_90d": 0.14 }
```

- Monitor chạy lại PD và R08 giám sát: đạt khi khoản trích của lịch `ACTIVE` ≤ `deduction_cap_ratio` × payout tuần hiện tại.
- R08 fail thì CashFlow Engine tính phương án giãn, prompt `monitor-proposal` viết D22, hồ sơ sang `RESTRUCTURE_PROPOSED`.
- Lịch `ACTIVE` giữ nguyên (FR-37). Nhãn test: `eval/golden/fx_l_monitor.json`.

### 6.6 `POST /v1/cases/{case_id}/restructure-decision`

Người ngân hàng duyệt hoặc giữ nguyên đề xuất D22.

```json
{ "expected_version": 5, "decision_type": "APPROVE_RESTRUCTURE", "proposal_id": "...", "reason": "..." }
```

- `decision_type`: `APPROVE_RESTRUCTURE` hoặc `KEEP_SCHEDULE`.
- `APPROVE_RESTRUCTURE` thêm version điều khoản `RESTRUCTURED_P7` / `ACTIVE`, đổi version trước sang `SUPERSEDED` (FR-38). Không ghi đè.
- `KEEP_SCHEDULE` không tạo version mới.
- Cần `Idempotency-Key` như đầu quyết định.

### 6.7 `GET /v1/studio/policy` và `GET /v1/studio/eval-metrics`

Chỉ đọc. Không có đầu sửa rule qua API.

- `policy`: nội dung `policy/policy.md`, `policy/parameters.yaml`, mã băm `rules.py` đang nạp, kết quả pytest, người xác nhận phiên bản (FR-18).
- `eval-metrics`: kết quả chạy `eval/golden/` và chi phí theo case. Số Gap, số tiền, pass/fail lấy từ code, không lấy từ Jev.

---

## 7. Chỗ code và tài liệu từng lệch

Ngày 23/10/2026 team chốt: số và rule theo code đang chạy. `policy/policy.md`, `policy/parameters.yaml`, `policy/worked-examples.md` và golden đã sửa theo code. Bảng dưới ghi từng chỗ và cách xử lý.

### 7.1 Đã chốt theo code

| Chỗ | Hành vi chuẩn (code) |
|---|---|
| Tên quyết định sửa số | `ACCEPT_COUNTER` |
| Tên trường số xin | `requested_amount` |
| Trần số xin | 300.000.000, Pydantic trả `422` kiểu dữ liệu |
| Trần tuần bán | `sell_weeks` ≤ 26 |
| Trần kỳ hạn | 26 tuần ở engine và R04 |
| Làm tròn số đề xuất | Lên bội 5.000.000, không vượt `gap × 1,15`. Gap 80.000.000 ra 90.000.000 |
| Bộ rule | R01 đến R09 trong `api/policy/rules.py`, nghĩa ở `policy/policy.md` |
| So R05 | Đòi sao kê khi `max(xin, đề xuất) > 2,5 ×` mức sàn. Bằng mốc thì không đòi |
| Trạng thái bàn giao | Hồ sơ giữ `ACCEPTED` hoặc `REJECTED`, kết quả gửi ở `handoff.status` |
| Lúc bàn giao | Người bấm `POST /handoff` |
| Đường dẫn bằng chứng | `/v1/cases/{id}/evidence/{evidence_id}` |
| Chi tiết hồ sơ | `/v1/cases/{id}` |
| Tạo hồ sơ | Chạy xong trong yêu cầu, trả trạng thái cuối |
| Mã bằng chứng | E4 gộp feature, E5 dòng tiền, E6 PD, E6a CIC, E7 rule, E8 sao kê |
| Hồ sơ bị chặn | Không lưu, trả `422 BLOCKED_INPUT` |
| Ghi chú seller | Có PII thì chặn cả hồ sơ |
| Sao kê lệch | R05 fail, `ESCALATED`, lý do `HARD_POLICY_FAILURE` |

### 7.2 Còn là kế hoạch

| Chỗ | Kế hoạch | Code hiện tại |
|---|---|---|
| Version điều khoản | Bảng `loan_terms_versions`, `SYSTEM_PROPOSED` / `HUMAN_MODIFIED`, bản cũ `SUPERSEDED` | Thêm `run` với `trigger = COUNTER` |
| Cờ fast-track | `is_fast_track`, hàng đợi lọc được | Chưa có |
| Sao kê | Seller upload file lên S3, mục 6.3 | Người thẩm định nhập tổng |
| Tiền ngoài sàn | `R06_OFF_PLATFORM_NOT_EVIDENCE` | Chưa phân biệt |
| Copilot, Monitor, Studio | Mục 6 | Chưa có |
