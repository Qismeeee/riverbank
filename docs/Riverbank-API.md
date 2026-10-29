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
- Mọi phản hồi có header `X-Request-ID`. FE gửi `X-Request-ID` (chữ, số, gạch nối, tối đa 64 ký tự) thì server dùng lại, không gửi thì server tự sinh. Báo lỗi kèm mã này để tra log CloudWatch `/riverbank/app`, mỗi request ghi một dòng JSON có `request_id`, `method`, `path`, `status`, `duration_ms`, `slow` (`slow` là true khi chậm hơn `RIVERBANK_SLOW_REQUEST_MS`, mặc định 5000).

### 1.2 Danh tính và vai trò

Mọi đầu `/v1` chỉ chạy khi `RIVERBANK_WORKFLOW_MODE=enabled`, nếu không thì trả `503 WORKFLOW_DISABLED`. Cách xác thực chọn bằng `RIVERBANK_WORKFLOW_IDENTITY`.

| Chế độ | Dùng ở | Cách gửi |
|---|---|---|
| `token` | Prod, FE Vercel | `Authorization: Bearer <access_token>` lấy từ `POST /v1/auth/token`. Header `X-Actor-*` bị bỏ qua |
| `header` (mặc định) | Chỉ local (`RIVERBANK_ENV=local`) | `X-Actor-ID`, `X-Actor-Role`. Ngoài local trả `503 VERIFIED_IDENTITY_ADAPTER_REQUIRED` |

**`POST /v1/auth/token`**, đăng nhập bằng tài khoản trong danh bạ và mã truy cập:

```json
{ "subject": "minhchau.shop", "role": "seller", "passcode": "<ACCESS_PASSCODE>" }
```

`subject` gồm chữ, số, `.`, `_`, `-`, tối đa 64 ký tự. Với seller, `subject` là `seller_id`. `subject` phải có trong danh bạ `api/data/users.json` (đổi bằng `RIVERBANK_USER_DIRECTORY`) với đúng `role`, sai thì `401 INVALID_CREDENTIALS` như sai mã. Phản hồi `200`:

```json
{
  "subject": "minhchau.shop",
  "role": "seller",
  "full_name": "Lê Minh Châu",
  "display_name": "Hộ kinh doanh Minh Châu Shop",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_at": "2026-10-24T00:00:00+00:00"
}
```

Token là JWT HS256, ký bằng `AUTH_SIGNING_KEY` (tối thiểu 32 byte), sống `RIVERBANK_AUTH_TOKEN_TTL_SECONDS` giây (mặc định 8 giờ). Prod đọc `AUTH_SIGNING_KEY` và `ACCESS_PASSCODE` từ secret `riverbank/auth`. Token của người đã bị xoá khỏi danh bạ trả `401 INVALID_TOKEN`.

**`GET /v1/auth/me`** trả `subject`, `role`, `full_name`, `display_name` của token đang dùng. Seller có `display_name` là tên hộ kinh doanh, người thẩm định có `display_name` là chức danh.

| Vai trò | Được gọi |
|---|---|
| `seller` | Tạo hồ sơ của chính mình, đọc `seller-view` của chính mình |
| `underwriter` | Hàng đợi, chi tiết hồ sơ, bằng chứng, nhập sao kê, quyết định, bàn giao |

Mã truy cập dùng chung cho mọi tài khoản trong danh bạ. Khi nối với ngân hàng thì thay `TokenIdentity` bằng adapter danh tính của ngân hàng, kế thừa `IdentityProvider` trong `api/routers/auth.py`. Không có vai trò hệ thống nào được gọi đầu quyết định (SRS FR-31).

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
| `429` | Sai mã đăng nhập nhiều lần, hoặc gọi quá ngưỡng tần suất. Có header `Retry-After` (giây) |
| `503` | Luồng `/v1` chưa bật, thiếu database |

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
| `POST` | `/v1/auth/token` | Không cần | Có | Đăng nhập, mục 1.2 |
| `GET` | `/v1/auth/me` | seller, underwriter | Có | Mục 1.2 |
| `GET` | `/v1/me/cases` | seller | Có | FR-27 |
| `POST` | `/v1/cases` | seller | Có | FR-01..FR-28 |
| `GET` | `/v1/cases/{case_id}/seller-view` | seller | Có | FR-27, FR-28 |
| `GET` | `/v1/queue` | underwriter | Có | FR-29 |
| `GET` | `/v1/cases/{case_id}` | underwriter | Có | FR-30 |
| `GET` | `/v1/cases/{case_id}/evidence/{evidence_id}` | underwriter | Có | FR-07, FR-30 |
| `POST` | `/v1/cases/{case_id}/statements` | underwriter | Có | FR-21..FR-23 |
| `POST` | `/v1/cases/{case_id}/statements/upload` | seller của hồ sơ, underwriter | Có | FR-22, FR-23 |
| `GET` | `/v1/cases/{case_id}/statements/original` | seller của hồ sơ, underwriter | Có | FR-22 |
| `GET` | `/v1/cases/{case_id}/statements/reading` | seller của hồ sơ, underwriter | Có | FR-22 |
| `POST` | `/v1/cases/{case_id}/statements/upload-jobs` | seller của hồ sơ, underwriter | Có | FR-22, FR-23 |
| `GET` | `/v1/jobs/{job_id}` | seller của hồ sơ, underwriter | Có | FR-22 |
| `POST` | `/v1/cases/{case_id}/cic-inquiry` | underwriter | Có | FR-16b |
| `POST` | `/v1/cases/{case_id}/decisions` | underwriter | Có | FR-31..FR-34 |
| `POST` | `/v1/cases/{case_id}/handoff` | underwriter | Có | FR-35, FR-36 |
| `POST` | `/v1/copilot/extract-plan` | seller | Có | FR-02, FR-03 |
| `POST` | `/v1/cases/{case_id}/copilot/ask` | seller của hồ sơ | Có | FR-27 |
| `GET` | `/v1/cases/{case_id}/copilot/history` | seller của hồ sơ, underwriter | Có | FR-27 |
| `POST` | `/v1/webhooks/los-status` | LOS ngân hàng | Chưa có | FR-36 |
| `POST` | `/v1/cases/{case_id}/simulate-p7-event` | underwriter | Chưa có | FR-37 |
| `POST` | `/v1/cases/{case_id}/restructure-decision` | underwriter | Chưa có | FR-38 |
| `GET` | `/v1/studio/policy` | underwriter | Có | FR-18 |
| `GET` | `/v1/studio/eval-metrics` | underwriter | Có | NFR-01..NFR-06 |
| `GET` | `/v1/studio/traffic` | underwriter | Có | NFR-01, NFR-06, mục 6.8 |

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
  "seller_id": "minhchau.shop",
  "shop_id": "minhchau.shop",
  "lot_value": 200000000,
  "cash_on_hand": 120000000,
  "supplier_schedule": [{ "week": 0, "amount": 200000000 }],
  "sell_weeks": 6,
  "requested_amount": 150000000,
  "note": ""
}
```

**Thứ tự xử lý:**

1. Quét `note`. Có lệnh bỏ qua chính sách hoặc ép duyệt, có SĐT, CCCD, số tài khoản, hoặc nhắc thuộc tính nhạy cảm thì trả `422 BLOCKED_INPUT`. `details` liệt kê từng lý do khớp, theo thứ tự `PHONE`, `NATIONAL_ID`, `ACCOUNT`, `PROMPT_INJECTION`, `PROHIBITED_ATTRIBUTE`. Mỗi phần tử có `reason`, `field: "note"` và `message`. Không tạo hồ sơ.
2. Kiểm tra khóa idempotency.
3. Đọc consent và feature của đúng `seller_id` + `shop_id`. Consent phải có `reference`, mục đích `WORKING_CAPITAL_UNDERWRITING`, chưa thu hồi, còn hạn. Thiếu thì `422 CONSENT_REQUIRED`.
4. Kiểm tra snapshot feature: đúng seller, có nguồn gốc payout (`observed` hoặc `estimated`), 4–104 tuần payout liên tục, mỗi tuần bắt đầu thứ Hai và đã kết thúc.
5. Chạy CashFlow Engine. Lưu bằng chứng `E4` (feature) và `E5` (dòng tiền).
6. Gap = 0: trạng thái `NO_LOAN_NEEDED`. Dừng ở đây, không gọi CIC.
7. Gap > 0: chấm PD (`E6`), hỏi Jev gate, đủ điều kiện mới tra CIC (`E6a`), chạy Policy Engine (`E7`). Gate từ chối hoặc lỗi thì không tra CIC, R06 fail thiếu dữ liệu.
   - Có hard fail khác R05 thiếu chứng từ, hoặc sao kê lệch: `ESCALATED`, lý do `HARD_POLICY_FAILURE`.
   - Snapshot thiếu hồ sơ shop nên không chấm được PD: `ESCALATED`, lý do `RISK_DATA_MISSING`. E6 ghi `missing_fields`.
   - Hard fail duy nhất là R05 thiếu chứng từ: `AWAITING_STATEMENT`.
   - Còn lại: `PENDING_REVIEW`.
8. Sinh tờ trình và bản seller từ template, kiểm từng câu với bằng chứng. Thử tối đa 3 lần. Không qua thì `ESCALATED`, lý do `MEMO_VALIDATION_FAILED`.

**Phản hồi `201`** (`CaseSummary`):

```json
{
  "case_id": "6f1d2c1e-6a3b-4a51-9d2a-1b7c2f0e9a11",
  "seller_id": "minhchau.shop",
  "shop_id": "minhchau.shop",
  "status": "PENDING_REVIEW",
  "version": 1,
  "reason": null,
  "created_at": "2026-10-23T08:00:00+00:00"
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
| `statement_file` | `null` khi chưa nộp file. Có thì gồm `media_type`, `pages`, `parser`, `lines`, `stored`. `stored` là file gốc còn tải được |
| `statement_period` | `null` khi không đang chờ sao kê. Có thì `start` và `end` là thứ Hai, khoảng `[start, end)` trùng các tuần payout của hồ sơ |

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

### 4.4b `GET /v1/me/cases`, Hồ sơ của seller

Vai trò `seller`. Trả hồ sơ của chính `subject`, mới nhất trước, mọi trạng thái. Tham số `limit` (1–100, mặc định 50), `offset` (≥ 0). Phản hồi cùng dạng với hàng đợi: `{ "items": [CaseSummary], "limit": 50, "offset": 0 }`.

### 4.5 `GET /v1/queue`, Hàng đợi

**Header:** `X-Actor-Role: underwriter`.

| Query | Kiểu | Mặc định |
|---|---|---|
| `status` | Một mã trạng thái | Không lọc |
| `limit` | 1–100 | 50 |
| `offset` | ≥ 0 | 0 |

Không truyền `status`: hàng đợi mặc định chỉ chứa hồ sơ chưa quyết (`PENDING_REVIEW`, `ESCALATED`, `NO_LOAN_NEEDED`, `AWAITING_STATEMENT`), sắp `created_at` tăng dần, `case_id` tăng dần (hồ sơ cũ lên trước).

`status=ACCEPTED` hoặc `status=REJECTED` là lối đọc hồ sơ đã thẩm định: trả đúng hồ sơ ở trạng thái đó, sắp `created_at` giảm dần, `case_id` giảm dần (hồ sơ mới lên trước). FE tự ghép hai danh sách, API không có mã gộp.

`status` là mã khác: trả hồ sơ ở trạng thái đó, sắp tăng dần.

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
| `trigger` | `INITIAL`, `STATEMENT`, `COUNTER`, hoặc `CIC_INQUIRY` |
| `input_snapshot`, `input_hash` | Plan, feature, consent đã dùng và mã băm |
| `engine_version`, `config` | Phiên bản và cấu hình CashFlow Engine |
| `policy_version`, `policy_hash` | Bộ rule đã chạy |
| `cashflow` | `payout_base`, `payout_stress`, `weeks`, `stress_weeks`, `gap`, `negative_weeks`, `amount_proposed`, `tenor_weeks`, `weekly_deduction`, `weekly_deductions`, `tenor_trace`, `payout_warning_threshold` |
| `risk` | `pd_bps` (`null` khi thiếu dữ liệu), `model_version`, `source` (`lightgbm` hoặc `baseline`), `factors[]` (`name`, `direction`, `contribution`, `value`), `missing_fields[]`, `disclosure`. `null` khi Gap = 0 |
| `cic` | `source` (`cic_profiles`, `fixed`, `not_queried`, hoặc `deferred_until_statement` khi hồ sơ cần sao kê và Jev chưa được hỏi, lúc đó `gate` là `null`), `bureau_group` (nhóm nợ hiện tại xấu nhất), `outstanding` (tổng dư nợ), `queried_at` (`null` khi không tra). Nguồn `cic_profiles` thêm `profiles_hash` (mã băm của `api/data/cic_profiles.json` lúc tra, sửa file là mã đổi, hồ sơ cũ giữ mã cũ), `worst_group_12m`, `has_credit_history`, `recent_inquiries_90d`, `facilities` (mỗi khoản: `institution`, `product`, `limit`, `outstanding`, `group`, `opened_month`, `history_12m` 12 tháng cũ đến mới), `gate` (`gate`, `eligible`, `threshold`, `model`, `probability`, `error`). `null` khi Gap = 0 |
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

Người thẩm định tự nhập tổng tiền sàn trên sao kê. Nộp file thì dùng mục 4.8a, hai đầu dùng chung cách đối chiếu dưới đây.

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

**Cách đối chiếu:** `platform_total` là tổng payout sàn các tuần trong khoảng. Lệch là `|platform_total − marketplace_total|`. Khớp khi lệch ≤ 15% `platform_total` (`statement_mismatch_ratio`). Hệ thống chạy lại thẩm định với kết quả này (`trigger = STATEMENT`), giữ nguyên CIC của lần trước. PD chấm lại trên cùng số nên không đổi.

- Khớp: R05 đạt, hồ sơ sang `PENDING_REVIEW` nếu không còn rule cứng nào fail.
- Lệch: `ESCALATED`. Thông điệp là "không khớp; cần xác minh". Không dùng từ trong `knowledge/guardrails.md`.

**Phản hồi `200`:** `CaseSummary`.

**Lỗi:** `409 STATEMENT_NOT_REQUESTED`, `409 CASE_VERSION_CONFLICT`, `422 INVALID_STATEMENT_PERIOD`, `422 STATEMENT_PERIOD_NOT_COVERED`.

### 4.8a `POST /v1/cases/{case_id}/statements/upload`, Nộp file sao kê

Seller của hồ sơ hoặc người thẩm định nộp file. Hệ thống bóc dòng, phân loại dòng nào là tiền sàn, code cộng tổng rồi đối chiếu như mục 4.8. Model không cộng, không tính phần trăm lệch.

**Header:** `Authorization: Bearer`. Seller khác hồ sơ thì `403 SELLER_SCOPE_MISMATCH`.

**Điều kiện:** như mục 4.8. Hệ thống kiểm điều kiện và kỳ trước khi đọc file, nên hồ sơ sai trạng thái không tốn lượt gọi model.

**Thân yêu cầu:** `multipart/form-data`.

| Trường | Kiểu | Ràng buộc |
|---|---|---|
| `expected_version` | int | ≥ 1 |
| `period_start`, `period_end` | date | Như mục 4.8 |
| `file` | file | CSV, Excel `.xlsx` và `.xls`, PDF, PNG, JPEG, WEBP, HEIC. Tối đa 10 MB. Loại file xác định theo byte đầu, không tin `Content-Type` của client |

**Cách đọc file** (lần lượt, dừng ở cách đầu tiên ra được dòng):

1. CSV: parser Python, tự nhận dấu phân cách `,` `;` tab.
1. Excel `.xlsx`: `openpyxl` đọc mọi sheet, ô ngày và ô số kiểu Excel đọc thẳng, header nằm ở dòng nào cũng được. Tối đa 50.000 dòng mỗi sheet. File zip không có `xl/workbook.xml` (ví dụ `.docx`) bị `415`.
1. Excel `.xls` đời cũ: `xlrd` đọc, ô ngày đổi đúng theo cài đặt ngày của file. File hỏng thì ghi lý do vào `notes` rồi `422`.
2. PDF có lớp chữ và kẻ bảng: `pdfplumber` đọc bảng, bảng tràn sang trang sau không lặp header vẫn đọc tiếp. Không gọi model, không tốn phí.
3. PDF có lớp chữ nhưng không kẻ bảng: đọc theo vị trí chữ. Cột lấy từ vị trí header, ô chữ cách nhau xa mới tách cột, nên số nằm trong mô tả (ví dụ "tuần 40") không bị hiểu là số tiền. Dòng mô tả xuống hàng được ghép vào giao dịch gần nhất.
4. PDF scan hoặc ảnh: prompt `statement-ocr` qua model đọc ảnh, mỗi trang một lượt, 4 trang chạy song song, tối đa `STATEMENT_OCR_MAX_PAGES` trang (mặc định 20). Ảnh được xoay theo EXIF, đổi HEIC và WEBP sang JPEG, thu cạnh dài về 2400 px trước khi gửi. Ảnh không mở được thì `422 STATEMENT_IMAGE_UNREADABLE`. Chỉ chạy khi `RIVERBANK_STATEMENT_OCR=llm`. Model đặt ở `STATEMENT_OCR_PROVIDER`, `STATEMENT_OCR_MODEL`. Prod dùng `openrouter` với `google/gemini-3.1-flash-lite`.

**PDF lẫn trang chữ và trang ảnh** (khi OCR bật): trang không có lớp chữ, hoặc ảnh phủ từ 50% diện tích trang mà không có chữ nào đọc ra ngày, được bỏ khỏi bước 2 và 3 rồi gửi riêng cho model. Dòng ghép lại theo thứ tự trang, `extraction.parser` là ví dụ `pdf_text+vision`, `extraction.image_pages` liệt kê trang đã gửi model. OCR tắt thì đọc như cũ.

**Tự kiểm bằng số dư:** có cột số dư thì code kiểm từng cặp dòng liền nhau: số dư trước + tiền vào - tiền ra = số dư sau, lệch tối đa 2 đ. Tự nhận sao kê xếp cũ đến mới hay mới đến cũ. Trang đọc bằng model mà lệch thì đọc lại ở độ nét cao hơn, chỉ lấy lần đọc lại khi ít dòng lệch hơn và không mất dòng, ghi `PAGE_n_RETRIED` vào `extraction.notes`. Kết quả ở `extraction.balance_check`: `checked_pairs`, `mismatched_lines` (chỉ số dòng trong `statement_lines`), `order`, `totals`.

**Kiểm tổng khi không có số dư từng dòng:** sao kê có dòng số dư đầu kỳ và cuối kỳ (đọc từ dòng tổng của bảng, hoặc model đọc từ ảnh) thì code kiểm số dư cuối - số dư đầu = tổng ghi có - tổng ghi nợ, lệch tối đa 2 đ. Kết quả ở `balance_check.totals`: `opening`, `closing`, `expected_change`, `read_change`, `matched`. Trang ảnh lệch tổng cũng được đọc lại như trên. Dòng tổng cộng không bị tính là giao dịch. Dòng lệch chỉ được gắn cờ cho người thẩm định, không tự đổi R05.

Cột được nhận theo từ khóa trong từng phần của header (header song ngữ như `Số tiền ghi có/Credit` tách theo `/`), tiếng Việt hoặc tiếng Anh, có dấu hoặc không: ngày hoặc thời gian, nội dung, diễn giải, mô tả, ghi có, phát sinh có, ghi nợ, phát sinh nợ, số dư (bỏ qua), hoặc một cột số tiền có dấu âm. Nhiều cột cùng vai trò thì chọn cột khớp cụm dài nhất, ví dụ `Debit` thắng `No.`. Không có header ngày thì lấy cột có từ 80% ô là ngày. Số tiền đọc được cả `70.000.000`, `70,000,000`, `70.000.000,00`.

**Phân loại dòng:** chỉ dòng tiền vào. `RIVERBANK_STATEMENT_CLASSIFIER=jev` (mặc định): regex `RIVERBANK_STATEMENT_PAYOUT_PATTERN` (mặc định `shopee`, so trên mô tả đã bỏ dấu) chỉ lọc ứng viên, Jev quyết từng ứng viên theo lô 20, ngưỡng 0,5. Nhờ vậy "NGUYEN VAN A chuyen tien hang shopee" không bị tính là payout chỉ vì có chữ Shopee. Jev lỗi thì `503`, không tự cho qua. `pattern` chỉ dùng regex, để chạy offline.

**Ngưỡng lệch:** `PolicyConfig.statement_mismatch_bps` (1500, tức 15%), dùng chung cho cả hai đầu sao kê. Nằm trong `policy_hash`.

**Cộng tổng:** `marketplace_total` là tổng ghi có của dòng tiền sàn có ngày trong `[period_start, period_end)`. Dòng tiền sàn thiếu ngày hoặc thiếu số tiền thì `422 STATEMENT_LINES_UNREADABLE`, `details` là chỉ số dòng, không đoán.

**Lưu file gốc:** `RIVERBANK_DOCUMENT_STORE=local` (mặc định) ghi file dưới `RIVERBANK_DOCUMENT_DIR` (mặc định `data/statements`), đường tương đối `{case_id}/{sha256}.{đuôi}`. `none` không lưu. `s3` lưu vào `S3_BUCKET` ở khóa `statements/{case_id}/{sha256}.{đuôi}`, mã hóa AES256. E8 ghi `original`. S3 lỗi thì `503 DOCUMENT_STORE_UNAVAILABLE`, thiếu bucket thì `503 DOCUMENT_STORE_NOT_CONFIGURED`. Trước khi bật `s3` trên prod, IAM role của EC2 phải có `s3:PutObject` và `s3:GetObject` trên bucket. Hồ sơ nộp lúc store là `none` không lấy lại được file gốc.

**Lưu:** E8 giữ `document_ref = sha256:<băm file>`, `source = uploaded_file`, `submitted_by`, `file` (`media_type`, `pages`), `extraction` (`parser`, `model`, `prompt_version`, `notes`), `classifier`, `coverage` (ngày đầu, ngày cuối trên sao kê) và `statement_lines` (từng dòng, đã che SĐT, CCCD, số tài khoản, kèm `is_payout`, `probability`, `in_period`).

**Phản hồi `200`:** `CaseSummary`.

**Lỗi:** như mục 4.8, thêm `413 STATEMENT_TOO_LARGE`, `415 STATEMENT_FORMAT_UNSUPPORTED`, `422 STATEMENT_EMPTY`, `422 STATEMENT_UNREADABLE` (`details` liệt kê từng cách đã thử), `422 STATEMENT_LINES_UNREADABLE`, `422 STATEMENT_TOO_MANY_PAGES`, `503 STATEMENT_OCR_UNAVAILABLE`, `503 STATEMENT_CLASSIFIER_UNAVAILABLE`.

```bash
curl -X POST $API/v1/cases/$C/statements/upload -H "Authorization: Bearer $T" \
  -F expected_version=1 -F period_start=2026-09-28 -F period_end=2026-10-12 -F file=@saoke.pdf
```

### 4.8c `POST /v1/cases/{case_id}/statements/upload-jobs`, Nộp sao kê chạy nền

Giống 4.8a nhưng không bắt client chờ OCR. Dùng cho PDF scan hoặc ảnh nhiều trang, có thể mất vài chục giây.

**Thân yêu cầu, quyền, điều kiện:** như 4.8a. Trước khi xếp hàng, API kiểm ngay quyền, `expected_version`, kỳ sao kê, kích thước và loại file, nên các lỗi `403`, `409`, `413`, `415` trả về luôn, không tạo job.

**Phản hồi `202`:** header `Location: /v1/jobs/{job_id}`, thân là `JobView`:

| Trường | Nghĩa |
|---|---|
| `job_id`, `kind`, `case_id`, `submitted_by`, `payload` | `kind` là `statement_upload`, `payload` là `expected_version`, `period_start`, `period_end` |
| `status` | `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED` |
| `attempts`, `max_attempts` | Số lần đã chạy, tối đa `RIVERBANK_JOB_MAX_ATTEMPTS` (mặc định 3) |
| `result` | Khi `SUCCEEDED`: `case_id`, `status`, `version` mới của hồ sơ |
| `error` | `code`, `status`, `details`, cùng mã lỗi như 4.8a |
| `created_at`, `updated_at` | ISO 8601 |

**Cách chạy:** job và file nằm trong bảng `jobs` của Postgres (memory khi chạy local). Worker là một thread trong tiến trình API, bật bằng `RIVERBANK_JOB_WORKER=on` (mặc định bật khi `RIVERBANK_ENV=prod`). Worker nhận job bằng `FOR UPDATE SKIP LOCKED`, nên chạy nhiều tiến trình không nhận trùng. Mỗi lần nhận có hạn `RIVERBANK_JOB_LEASE_SECONDS` (mặc định 600). Tiến trình chết giữa chừng thì hết hạn xong job được nhận lại. Lỗi `5xx` (model, Jev, S3 lỗi) được thử lại. Lỗi `4xx` (file không đọc được, hồ sơ đã đổi version) dừng luôn. Job xong thì xóa file khỏi bảng. Chạy lại một job đã ghi sao kê không ghi lần hai, E8 có `job_id` để đối chiếu.

### 4.8d `GET /v1/jobs/{job_id}`, Xem trạng thái job

Seller của hồ sơ hoặc người thẩm định. Phản hồi `200`: `JobView`. Không có thì `404 JOB_NOT_FOUND`. FE gọi lại mỗi 2 giây đến khi `SUCCEEDED` hoặc `FAILED`, rồi tải lại hồ sơ.

```bash
curl -X POST $API/v1/cases/$C/statements/upload-jobs -H "Authorization: Bearer $T" \
  -F expected_version=1 -F period_start=2026-09-28 -F period_end=2026-10-12 -F file=@saoke.pdf
curl $API/v1/jobs/$J -H "Authorization: Bearer $T"
```

### 4.8e Tải file gốc và kết quả đọc

Seller của hồ sơ hoặc người thẩm định. Lấy bản sao kê nộp file mới nhất (`source = uploaded_file`). Nhập tổng tay không có file thì `404 STATEMENT_NOT_FOUND`.

`GET /v1/cases/{case_id}/statements/original` trả đúng byte đã nộp, `Content-Type` theo loại file, `Content-Disposition: attachment`. Store lúc nộp khác store đang chạy, hoặc store là `none`, thì `404 STATEMENT_FILE_NOT_STORED`.

`GET /v1/cases/{case_id}/statements/reading` trả JSON đính kèm: `file`, `extraction`, `statement_lines` (đã che), `coverage`, `classifier`, và các số đối chiếu code đã ghi (`marketplace_total`, `platform_total`, `difference`, `matched`). Không kèm vị trí lưu file.

```bash
curl -OJ $API/v1/cases/$C/statements/original -H "Authorization: Bearer $T"
curl -OJ $API/v1/cases/$C/statements/reading -H "Authorization: Bearer $T"
```

---

### 4.8b `POST /v1/cases/{case_id}/cic-inquiry`, Người duyệt tra CIC thủ công

Dùng khi Jev gate từ chối hoặc lỗi nên hệ thống chưa tra CIC (`cic.source = not_queried`, R06 fail thiếu dữ liệu). Người duyệt chịu trách nhiệm, hệ thống tra CIC bỏ qua Jev rồi chạy lại thẩm định (`trigger = CIC_INQUIRY`).

**Header:** vai trò `underwriter`.

**Điều kiện:** hồ sơ `ESCALATED`, chưa có quyết định, lần chạy cuối có `cic.source` là `not_queried` hoặc `deferred_until_statement`. Không thỏa thì `409 CIC_INQUIRY_NOT_ALLOWED`.

**Thân yêu cầu** (`CicInquiry`):

| Trường | Kiểu | Ràng buộc |
|---|---|---|
| `expected_version` | int | ≥ 1 |
| `reason` | string | 1–2000, bắt buộc. Lý do người duyệt cho tra |

**Kết quả:** E6a mới giữ nguyên `gate` của Jev và thêm `override` (`actor`, `reason`, `requested_at`). R06, R07 chạy trên CIC thật. Sự kiện `CIC_INQUIRY_OVERRIDE` vào `case_events`. Lần chạy sau đó (sao kê, duyệt số khác) dùng lại E6a này.

**Phản hồi `200`:** `CaseSummary`.

**Lỗi:** `409 CIC_INQUIRY_NOT_ALLOWED`, `409 CASE_VERSION_CONFLICT`, `403 UNDERWRITER_REQUIRED`.

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
   - Chạy lại toàn bộ rule trên bộ số cuối. Có hard fail thì `409 HARD_POLICY_FAILURE`, `details` là danh sách mã rule (FR-32). Riêng R10 fail vì thiếu PD (hồ sơ `RISK_DATA_MISSING`) không chặn, vì đã bắt buộc `verification_note`. R10 vẫn ghi fail trong `policy_results_final`.
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
3. Gửi LOS với `idempotency_key` cố định của gói. `InternalLosAdapter` trả mã `LOS-<key>`.
4. Ghi `SUCCEEDED` kèm `los_reference`, hoặc `FAILED` kèm `last_error` (`LOS_UNAVAILABLE`, `LOS_INVALID_RESPONSE`). Quyết định của người không bị đổi khi gửi lỗi (FR-36). Gọi lại đầu này để thử lại.

Đã `SUCCEEDED` thì gọi lại trả kết quả cũ, không gửi lần hai.

**Gói gửi LOS** (`handoff.payload`): `case_id`, `seller_id`, `shop_id`, `decision`, `risk`, `cic`, `policy`, `document` (tờ trình đã qua), `evidence_ids`. Gói `REJECT` có `final_amount = null` (FR-35).

**Phản hồi `200`** (`HandoffResponse`): `CaseSummary` thêm `handoff` không kèm `payload`:

```json
{
  "idempotency_key": "...",
  "status": "SUCCEEDED",
  "payload_hash": "...",
  "attempts": 1,
  "last_error": null,
  "los_reference": "LOS-...",
  "lease_until": null
}
```

Riverbank không gửi lệnh giải ngân, hạch toán, hay trích nợ sang core (FR-37 bản Riverbank).

---

## 5. Mã lỗi

| Mã | HTTP | Đầu API | Nghĩa |
|---|---|---|---|
| `ROLE_NOT_ALLOWED` | 403 | `/v1/*` | Vai trò không phải `seller` hay `underwriter` |
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
| `CIC_INQUIRY_NOT_ALLOWED` | 409 | Tra CIC thủ công | Hồ sơ không `ESCALATED`, đã có quyết định, hoặc CIC đã được tra |
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
| `BLOCKED_INPUT` | 422 | Tạo hồ sơ, rút kế hoạch | Ghi chú hoặc câu chat vi phạm cổng vào. `details[]` có `reason` và `message` |
| `CONSENT_REQUIRED` | 422 | Tạo hồ sơ | Không có consent hiệu lực |
| `PAYOUT_HISTORY_REQUIRED` | 422 | Tạo hồ sơ | Dưới 4 hoặc trên 104 tuần payout |
| `INVALID_PAYOUT_PERIOD` | 422 | Tạo hồ sơ | `week_start` sai định dạng |
| `INVALID_PAYOUT_HISTORY` | 422 | Tạo hồ sơ | Tuần không liên tục, chưa kết thúc, hoặc số âm |
| `UNAPPROVED_FEATURE_FIELDS` | 422 | Tạo hồ sơ | Snapshot có trường ngoài danh sách cho phép |
| `PAYOUT_PROVENANCE_REQUIRED` | 422 | Tạo hồ sơ | Thiếu nguồn gốc payout |
| `ESTIMATE_MUST_BE_LABELLED` | 422 | Tạo hồ sơ | Nguồn `addlivetag` hoặc `seller_book` không gắn `payout_origin = estimated` |
| `INVALID_FEATURE_SNAPSHOT` | 422 | Tạo hồ sơ | Thiếu mức dữ liệu sàn hỗ trợ hoặc lời công bố nguồn, hoặc hồ sơ shop sai kiểu, ngoài khoảng |
| `UNSUPPORTED_SHOPEE_SOURCE` | 422 | Tạo hồ sơ | Nguồn Shopee chưa hỗ trợ |
| `INVALID_RISK_RESULT` | 422 | Tạo hồ sơ | PD ngoài khoảng, thiếu yếu tố, hoặc báo thiếu dữ liệu mà vẫn có PD |
| `INVALID_STATEMENT_PERIOD` | 422 | Sao kê | Ngày sai định dạng |
| `STATEMENT_PERIOD_NOT_COVERED` | 422 | Sao kê | Kỳ không trùng trọn các tuần payout |
| `STATEMENT_EMPTY`, `STATEMENT_UNREADABLE`, `STATEMENT_LINES_UNREADABLE`, `STATEMENT_TOO_MANY_PAGES` | 422 | Nộp sao kê | File rỗng, không đọc được bảng, dòng tiền sàn thiếu ngày hoặc số, quá 10 trang |
| `STATEMENT_TOO_LARGE` | 413 | Nộp sao kê | File trên 10 MB |
| `STATEMENT_FORMAT_UNSUPPORTED` | 415 | Nộp sao kê | Không phải CSV, XLSX, PDF, PNG, JPEG |
| `STATEMENT_OCR_UNAVAILABLE`, `STATEMENT_CLASSIFIER_UNAVAILABLE` | 503 | Nộp sao kê | Model đọc ảnh hoặc Jev lỗi |
| `STATEMENT_IMAGE_UNREADABLE` | 422 | Nộp sao kê | Ảnh hỏng hoặc không mở được |
| `DOCUMENT_STORE_UNAVAILABLE`, `DOCUMENT_STORE_NOT_CONFIGURED` | 503 | Nộp sao kê, tải file gốc | S3 lỗi hoặc thiếu `S3_BUCKET` khi bật lưu file gốc |
| `STATEMENT_NOT_FOUND` | 404 | Tải sao kê | Hồ sơ chưa có file sao kê đã nộp |
| `STATEMENT_FILE_NOT_STORED` | 404 | Tải file gốc | Lần nộp đó không giữ file, hoặc store hiện tại không đọc được bản ghi |
| `JOB_NOT_FOUND` | 404 | `/v1/jobs/{job_id}` | Không có job |
| `JOB_LEASE_EXPIRED`, `JOB_HANDLER_ERROR`, `JOB_KIND_UNKNOWN` | 500 | Trong `error` của job | Worker chết quá số lần, lỗi không lường trước, loại job lạ |
| `STATEMENT_READER_UNAVAILABLE`, `STATEMENT_OCR_UNKNOWN`, `STATEMENT_OCR_NOT_CONFIGURED`, `STATEMENT_CLASSIFIER_UNKNOWN` | 503 | `/v1/*` | Cấu hình đọc sao kê sai hoặc thiếu key |
| `VERIFIED_IDENTITY_ADAPTER_REQUIRED` | 503 | `/v1/*` | Chế độ `header` chỉ chạy local |
| `WORKFLOW_DISABLED` | 503 | `/v1/*` | `RIVERBANK_WORKFLOW_MODE` khác `enabled` |
| `INVALID_WORKFLOW_STORAGE`, `INVALID_WORKFLOW_FEATURES`, `INVALID_WORKFLOW_RISK`, `INVALID_WORKFLOW_CIC_GATE`, `INVALID_WORKFLOW_CIC_SOURCE`, `CIC_PROFILES_INVALID` | 503 | `/v1/*` | Cấu hình sai |
| `DATABASE_REQUIRED` | 503 | `/v1/*` | Chọn Postgres nhưng thiếu `DATABASE_URL` |
| `AUTHENTICATION_REQUIRED` | 401 | `/v1/*` | Thiếu danh tính hoặc thiếu `Authorization: Bearer` |
| `INVALID_TOKEN`, `TOKEN_EXPIRED` | 401 | `/v1/*` | Token sai chữ ký, sai định dạng, hoặc hết hạn |
| `INVALID_CREDENTIALS` | 401 | `/v1/auth/token` | Sai mã truy cập, hoặc `subject` và `role` không có trong danh bạ |
| `USER_DIRECTORY_INVALID` | 503 | `/v1/auth/*`, `/v1/*` | File danh bạ thiếu, sai định dạng, trùng `subject` hoặc thiếu họ tên |
| `TOO_MANY_ATTEMPTS` | 429 | `/v1/auth/token` | Sai mã quá `RIVERBANK_AUTH_MAX_FAILURES` lần (mặc định 5) trong `RIVERBANK_AUTH_FAILURE_WINDOW_SECONDS` giây (mặc định 300) từ một IP |
| `RATE_LIMITED` | 429 | Tạo hồ sơ, nộp sao kê (cả 2 đầu), Copilot extract-plan và ask | Một người gọi vượt ngưỡng trong cửa sổ trượt. Header `Retry-After` và `details[0].retry_after_seconds` cho biết số giây phải chờ. Ngưỡng dạng `số lần/giây`: `RIVERBANK_RATE_LIMIT_CASE_CREATE` (mặc định `30/3600`), `RIVERBANK_RATE_LIMIT_STATEMENT_UPLOAD` (`20/3600`, hai đầu nộp sao kê dùng chung), `RIVERBANK_RATE_LIMIT_COPILOT` (`20/60`). `0/...` là tắt. Đếm theo từng tiến trình, server chạy 2 tiến trình nên ngưỡng thật tối đa gấp đôi |
| `SELLER_REQUIRED` | 403 | `/v1/me/cases` | Không phải vai trò seller |
| `EVAL_REPORT_MISSING` | 503 | `/v1/studio/eval-metrics` | Thiếu `api/studio/eval_report.json` |
| `TRAFFIC_WINDOW_INVALID` | 422 | `/v1/studio/traffic` | `from` sau `to`, hoặc ngày sai `YYYY-MM-DD` |
| `TRAFFIC_WINDOW_TOO_WIDE` | 503 | `/v1/studio/traffic` | Cửa sổ có trên 5.000 hồ sơ |
| `TOKEN_LOGIN_DISABLED` | 503 | `/v1/auth/token` | Không ở chế độ `token` |
| `AUTH_NOT_CONFIGURED` | 503 | `/v1/auth/*`, `/v1/*` | Thiếu `ACCESS_PASSCODE` hoặc `AUTH_SIGNING_KEY` ngắn hơn 32 byte |
| `INVALID_WORKFLOW_IDENTITY` | 503 | `/v1/*` | `RIVERBANK_WORKFLOW_IDENTITY` sai |
| `OPENROUTER_KEY_REQUIRED` | 503 | `/v1/*` | Bật Jev gate CIC nhưng thiếu `OPENROUTER_API_KEY` |

---

## 6. Copilot và API trong kế hoạch

Mục 6.1, 6.2, 6.3 đã có code. Từ 6.4 trở đi là kế hoạch, chưa có code. Hợp đồng dưới đây theo quy ước mục 1. Đầu nào có gọi model phải đi đúng chỗ ở bảng "Chỗ nào được gọi model" trong `KNOWLEDGE_BASE.md`.

### 6.1 `POST /v1/copilot/extract-plan`

Seller chat tự do, nhận lại bản nháp đúng dạng body của `POST /v1/cases`. Không tạo hồ sơ (FR-02). Header như `POST /v1/cases`, `X-Actor-ID` phải bằng `seller_id`.

```json
{ "seller_id": "minhchau.shop", "shop_id": "minhchau.shop", "message": "Mình nhập lô 200tr, có 120tr, trả hết tuần 0, bán 6 tuần, xin 150tr" }
```

Chat nhiều lượt: lượt sau gửi thêm `known` là `draft` của lượt trước. Câu mới chỉ cần nói phần còn thiếu hoặc phần muốn sửa.

```json
{ "seller_id": "minhchau.shop", "shop_id": "minhchau.shop", "message": "à xin 120tr thôi", "known": { "lot_value": 200000000, "cash_on_hand": 120000000, "supplier_schedule": [{ "week": 0, "amount": 200000000 }], "sell_weeks": 6, "requested_amount": 150000000 } }
```

```json
{
  "draft": {
    "seller_id": "minhchau.shop",
    "shop_id": "minhchau.shop",
    "lot_value": 200000000,
    "cash_on_hand": 120000000,
    "supplier_schedule": [{ "week": 0, "amount": 200000000 }],
    "sell_weeks": 6,
    "requested_amount": 150000000
  },
  "ready": true,
  "missing_fields": [],
  "issues": [],
  "clarifying_question": null,
  "reply": "Mình đã ghi giá trị lô hàng 200.000.000 đ, ... Đủ thông tin rồi, bạn bấm Gửi để hệ thống tính.",
  "sanitized_message": "...",
  "prompt_version": "copilot-extract@<sha8>",
  "model": "..."
}
```

- Che SĐT, CCCD, số tài khoản trên `message` trước khi gọi Jev hoặc LLM (FR-03).
- Jev `jev-input-guard` trên chữ đã che. Xác suất ≥ `guardrail.block_probability` thì `422 BLOCKED_INPUT`. `details` có `reason` (`PROMPT_INJECTION` hoặc `PROHIBITED_ATTRIBUTE`), `field: "message"`, `message`, `flag`, `probability`, `threshold`. Jev lỗi thì `503 GUARD_UNAVAILABLE`, không cho qua.
- Prompt `copilot-extract` trả JSON theo schema cố định. Code kiểm lại: số tiền và số tuần phải có trong câu đã che, lịch xưởng phải cộng đúng `lot_value`. Sai thì trường đó về `null`, vào `missing_fields`, kèm `clarifying_question`.
- Giá trị trong `known` chỉ được giữ khi câu mới không nói khác. Số mới mà không có trong câu thì bỏ, quay về số trong `known`. Lịch xưởng cũ chỉ giữ khi vẫn cộng đúng `lot_value` mới.
- `reply` là câu trả lời để hiện trong khung chat, do code ghép từ `draft`, `clarifying_question`, `issues`. Không phải chữ LLM tự viết, nên số trong `reply` luôn khớp `draft`.
- Đủ năm trường thì code chạy validator của `Plan`. Lỗi (ví dụ xin quá 300 triệu) nằm ở `issues` dạng `{field, code}`, số seller nói vẫn giữ nguyên.
- `ready = true` thì client gửi nguyên `draft` vào `POST /v1/cases`. LLM lỗi hoặc trả sai dạng thì `503 EXTRACTION_UNAVAILABLE`.

### 6.2 `POST /v1/cases/{case_id}/copilot/ask`

Seller hỏi lại trên số đã khóa của hồ sơ. Chỉ seller chủ hồ sơ gọi được, seller khác `403`. Server tự lưu mọi lượt hỏi đáp theo hồ sơ, FE không gửi lịch sử. Body có trường lạ (ví dụ `history`) bị `422`.

```json
{ "question": "Sao chỉ được vay ít hơn số mình xin?" }
```

```json
{
  "case_id": "...",
  "turn_id": "...",
  "question": "Sao chỉ được vay ít hơn số mình xin?",
  "status": "PENDING_REVIEW",
  "answer": "Hệ thống đề xuất 90.000.000 đ trong 8 tuần, mỗi tuần trừ 11.250.000 đ từ payout.",
  "figures_used": ["amount_proposed", "tenor_weeks", "weekly_deduction"],
  "source": "llm",
  "guard_codes": [],
  "prompt_version": "copilot-qa@<sha8>",
  "model": "...",
  "asked_at": "2026-10-25T14:30:00+00:00"
}
```

- Che PII trên câu hỏi, rồi mới lưu và gửi đi. `question` trả về là bản đã che.
- 6 lượt gần nhất của hồ sơ được gửi kèm cho LLM để hiểu câu hỏi nối tiếp. Lượt bị Jev chặn không được lưu.
- Câu hỏi đã che qua Jev `jev-input-guard` như 6.1 (`422 BLOCKED_INPUT`, `503 GUARD_UNAVAILABLE`).
- Prompt `copilot-qa` chỉ thấy bộ số an toàn cho seller của hồ sơ: trạng thái, mã lý do, các câu giải thích seller đã qua kiểm tra, khoảng tuần cần nộp sao kê (khi R05 fail), số trong kế hoạch, gap, số đề xuất, kỳ hạn, khoản trừ mỗi tuần, sao kê khớp hay không, số chốt, mã giải ngân. Không có PD, không có CIC, không có ghi chú của người thẩm định.
- LLM phải kê `figures_used`. Code kiểm: tên số phải có giá trị, mọi con số trong câu phải nằm trong các số đã kê (viết đủ hoặc theo triệu), qua `OutputGuard` (từ cấm, câu nói ngược trạng thái). Không nêu lãi và phí khi chưa có biểu phí ngân hàng (FR-27).
- LLM lỗi hoặc câu trả lời vi phạm thì không lỗi `503`: trả câu mẫu do code ghép, `source = "template"`, `guard_codes` ghi lý do, `model = null`. Câu mẫu cũng nói bước tiếp theo khi cần sao kê.

`GET /v1/cases/{case_id}/copilot/history?limit=50` trả `{ "case_id", "turns": [...] }`, mỗi phần tử như phản hồi `ask` trừ `case_id`, cũ đến mới, `limit` từ 1 đến 100. Seller chỉ xem hồ sơ của mình, underwriter xem mọi hồ sơ. Lưu ở bảng `copilot_turns` (migration `002`), không sửa, không xóa được. Chạy `memory` thì mất khi API khởi động lại.

### 6.3 Nộp file sao kê

Đã có ở mục 4.8a, bản chạy nền ở 4.8c. File vẫn đi thẳng qua API, giới hạn 10 MB. Lưu bản gốc lên S3 bật bằng `RIVERBANK_DOCUMENT_STORE=s3`. Chưa có S3 presign cho file lớn.

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

Chưa có. Dùng cho người thẩm định nhập sự kiện payout tuần tụt để chạy Monitor.

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

Đã có, chỉ đọc, vai trò `underwriter`. Không có đầu sửa rule qua API.

- `policy` đọc thẳng từ service đang chạy: `policy_version`, `policy_hash` (băm `PolicyConfig`, `rules.py`, `engine.py`), `engine_version`, `policy_config`, `cashflow_config`, `rules[]` (`rule_id`, `hard`, `order` theo thứ tự chạy), `risk_model` (nguồn, phiên bản, feature, metric từ `pd_meta.json`), `cic_gate` (`gate`, `model`, `threshold`). Chưa có người xác nhận phiên bản (FR-18).
- `eval-metrics` trả `api/studio/eval_report.json`: `golden_digest`, `summary` (`total`, `passed`, `failed`), `cases[]` (`case_id`, `passed`, `mismatched_keys`, `expected_status`, `status`, `gap`, `amount_proposed`, `cic_called`, `pd_scored`). File sinh bằng `make eval-report` từ `eval/golden/` qua `UnderwritingService` thật. `test_golden.py` đỏ khi file lệch golden. Chi phí và thời gian từng hồ sơ không nằm ở đầu này. Xem mục 6.8. Thiếu file thì `503 EVAL_REPORT_MISSING`.

### 6.8 `GET /v1/studio/traffic`

Chỉ đọc, vai trò `underwriter`, cùng cách xác thực với mục 6.7. Không có thân yêu cầu. Không sửa hồ sơ, không ghi `case_events`.

Đầu này trả lưu lượng hồ sơ, thời gian lần tính, và chi phí model. Ba số đó là code tính. LLM không ghi thời gian, không ghi token, không ghi tiền.

FE hiện không có đầu này. Tab Lưu lượng tự gọi `GET /v1/queue` (hàng đang mở, rồi `ACCEPTED`, rồi `REJECTED`) và `GET /v1/cases/{case_id}` từng hồ sơ. Thời gian FE đang lấy là hiệu `events[1].occurred_at − events[0].occurred_at` khi hiệu nằm trong 3 phút. Cách đó không phải hợp đồng. Chi phí FE để trống vì hồ sơ không lưu token. Khi đầu này có, FE chỉ gọi đầu này.

#### Việc phải ghi lúc chạy, không phải đầu API mới

`LlmResponse.usage` đã có trên client OpenAI (`prompt_tokens`, `completion_tokens`, `total_tokens`) nhưng `MemoWriter.write` chỉ giữ `model`. `documents[]` vì thế có `model`, không có usage. Jev (`cic_gate`, phân loại dòng sao kê) cũng không ghi usage.

Mỗi lần gọi model gắn với một `run` thì thêm một dòng, chỉ thêm, không sửa dòng cũ:

| Trường | Nội dung |
|---|---|
| `call_id` | UUID |
| `case_id`, `run_id` | Hồ sơ và lần chạy. Cả hai bắt buộc |
| `purpose` | `memo_writer`, `statement_ocr`, `statement_classifier`, `cic_gate` |
| `provider` | `openai` hoặc `openrouter` |
| `model` | Đúng chuỗi model trả về |
| `prompt_tokens`, `completion_tokens`, `total_tokens` | Số nguyên, hoặc `null` khi provider không trả usage |
| `cached` | `true` khi lấy từ cache local |
| `cost_micro_usd` | Số nguyên, hoặc `null` khi thiếu usage hoặc thiếu đơn giá |
| `occurred_at` | ISO 8601 |

Đơn giá nằm ở `api/studio/model_prices.yaml`, code đọc. Không lấy từ `policy/parameters.yaml` (file đó chỉ là ngưỡng nghiệp vụ). Không để LLM điền tiền. Không ước token từ số ký tự. Model chưa có dòng đơn giá thì `cost_micro_usd` là `null`.

`cost_micro_usd` là phần triệu USD: 1 USD = 1.000.000. Trần NFR-06 là 0,10 USD, tức `100000`.

Mỗi lần gọi `complete_json` hoặc `jev.decide` trong một lần chạy là một dòng, kể cả lần viết tờ trình bị validator bắt làm lại. Tờ trình template và phân loại không qua model thì không có dòng.

Copilot (`extract-plan`, hỏi lại) không gắn lần chạy thẩm định. Không đưa vào đầu này.

Trên chính `run`, lúc bắt đầu và lúc ghi xong lần chạy, thêm `started_at`, `finished_at`, `duration_ms`. `duration_ms` là số nguyên, đồng hồ máy chủ, trọn lần chạy đó (kể cả viết lại tờ trình). Không lấy từ `case_events`. Quyết định của người ngân hàng nằm sau lần chạy, không cộng vào `duration_ms`.

Hồ sơ tạo trước khi có các trường này vẫn có trong lưu lượng. `duration_ms` là `null`. Không suy thời gian từ hai event đầu.

#### Tham số

| Query | Kiểu | Mặc định |
|---|---|---|
| `from`, `to` | Ngày `YYYY-MM-DD`, tính theo `Asia/Ho_Chi_Minh`, cả hai đầu tính | Bỏ cả hai thì lấy mọi hồ sơ |
| `recent` | 1–50 | 12 |

`from` sau `to`, hoặc ngày sai dạng, thì `422 TRAFFIC_WINDOW_INVALID`. Cửa sổ có trên 5.000 hồ sơ thì `503 TRAFFIC_WINDOW_TOO_WIDE`.

Ngày lọc theo `created_at` của hồ sơ, không theo lúc người bấm quyết định.

#### Phản hồi `200`

```json
{
  "zone": "Asia/Ho_Chi_Minh",
  "generated_at": "2026-10-27T04:18:00+00:00",
  "targets": { "latency_ms": 90000, "cost_micro_usd": 100000 },
  "summary": {
    "cases": 51,
    "opened_today": 4,
    "measured_runs": 48,
    "unmeasured_runs": 3,
    "latency_ms": { "p50": 3500, "p95": 5400 },
    "cost": {
      "known_cases": 40,
      "unknown_cases": 11,
      "total_micro_usd": 180000,
      "p50_micro_usd": 4000
    }
  },
  "by_day": [{ "day": "2026-10-27", "cases": 4 }],
  "by_status": [{ "status": "ACCEPTED", "cases": 18 }],
  "by_model": [{ "model": "gpt-4.1-mini-2025-04-14", "calls": 48, "cases": 40 }],
  "recent": [
    {
      "case_id": "4789881e-0000-4000-8000-000000000000",
      "seller_id": "demo-i",
      "status": "REJECTED",
      "reason": "HARD_POLICY_FAILURE",
      "created_at": "2026-10-27T03:27:11+00:00",
      "first_run": {
        "run_id": "...",
        "trigger": "INITIAL",
        "started_at": "2026-10-27T03:27:11+00:00",
        "finished_at": "2026-10-27T03:27:15+00:00",
        "duration_ms": 3650
      },
      "models": ["gpt-4.1-mini-2025-04-14"],
      "cost_micro_usd": 4200,
      "cost_known": true
    }
  ]
}
```

Số trong ví dụ chỉ để chỉ kiểu. Không hard-code các số đó trong test.

Cách tính, làm trong service, không làm ở FE:

| Khối | Cách tính |
|---|---|
| `summary.cases` | Mọi hồ sơ trong cửa sổ, kể cả `ACCEPTED` và `REJECTED` |
| `opened_today` | `created_at` rơi vào ngày hôm nay theo `zone`, lấy đồng hồ máy chủ |
| `by_day` | Đếm theo ngày `created_at` trong `zone`, ngày tăng dần, bỏ ngày không có hồ sơ |
| `by_status` | Đếm theo `status`, nhiều trước |
| `latency_ms` | Chỉ lần chạy đầu (`run_no` nhỏ nhất, `trigger = INITIAL`) có `duration_ms`. Hồ sơ chưa đo không đưa vào mẫu. `measured_runs` là số hồ sơ có số, `unmeasured_runs` là số còn lại. Mẫu rỗng thì `p50` và `p95` là `null` |
| `p50`, `p95` | Xếp tăng dần. Vị trí `min(n − 1, max(0, ceil(p / 100 × n) − 1))` |
| `cost` | Cộng `cost_micro_usd` của mọi lần gọi trên mọi `run` của hồ sơ. `cost_known` chỉ khi hồ sơ được ghi sau khi có metering và mọi dòng gọi đều có tiền. Lần chạy xong mà không gọi model nào thì tiền là `0` và `cost_known` là `true`. Thiếu một dòng đơn giá hoặc thiếu usage thì cả hồ sơ `cost_known = false` và không cộng vào `total_micro_usd`. Không có hồ sơ nào `cost_known` thì `total_micro_usd` và `p50_micro_usd` là `null`, không phải `0` |
| `by_model` | Nhóm theo `model` của dòng gọi. `calls` là số dòng, `cases` là số hồ sơ phân biệt. Tên model cũ trên `documents[].model` chỉ để hiện ở `recent.models` khi chưa có dòng gọi. Tên đó không làm `cost_known` thành `true` và không vào `by_model` |
| `recent` | Mới nhất theo `created_at`, tối đa `recent` phần tử |

`targets` chép NFR-01 (90 giây) và NFR-06 (0,10 USD). Đây là mốc so sánh, không phải kết quả đo.

Phản hồi không có câu tờ trình, không có plan, không có bằng chứng, không có số điện thoại, CCCD, số tài khoản.

Seller gọi đầu này thì `403`, cùng các đầu Studio khác. Lỗi danh tính dùng các mã mục 5.

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
| Bộ rule | R01 đến R10 trong `api/policy/rules.py`, nghĩa ở `policy/policy.md` |
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
| Monitor | Mục 6 | Chưa có |
