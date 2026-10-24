# Nối FE Vercel với API Riverbank

Dành cho người làm `https://ezxriverbank.vercel.app`. Chi tiết từng trường ở `docs/Riverbank-API.md`, OpenAPI sống ở `<API>/docs`.

## 1. Địa chỉ và CORS

- API prod: `https://13-214-87-165.sslip.io`. Mọi đầu nghiệp vụ nằm dưới `/v1`.
- Origin được phép đọc từ `ALLOWED_ORIGINS` trên EC2. Phải có `https://ezxriverbank.vercel.app`, nếu không trình duyệt chặn mọi lời gọi.
- Gửi và nhận JSON UTF-8. Tiền là số nguyên VND, không gửi chuỗi.

## 2. Đăng nhập

```js
const API = "https://13-214-87-165.sslip.io";

async function login(subject, role, passcode) {
  const r = await fetch(`${API}/v1/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subject, role, passcode }),
  });
  if (!r.ok) throw (await r.json()).detail;
  return r.json();
}
```

- Seller: `subject` là `seller_id` (`demo-a`, `demo-b`, `demo-c`, `demo-d`), `role: "seller"`. `demo-d` cùng số với `demo-b` nhưng hồ sơ shop rủi ro hơn, PD vượt trần nên R10 fail và hồ sơ sang `ESCALATED`.
- Người thẩm định: `subject` là mã người duyệt bất kỳ, ví dụ `uw-01`, `role: "underwriter"`.
- `passcode` là mã demo chung, chủ dự án đưa riêng. Không ghi mã vào code FE.
- Lưu `access_token` trong bộ nhớ trang hoặc `sessionStorage`. Hết hạn (`expires_at`, mặc định 8 giờ) thì đăng nhập lại.

```js
async function call(token, method, path, body, extraHeaders = {}) {
  const r = await fetch(`${API}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...extraHeaders,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json();
  if (!r.ok) throw { status: r.status, ...data.detail };
  return data;
}
```

## 3. Màn hình và đầu API

### 01 Seller Portal

| Bước | Gọi |
|---|---|
| Gửi kế hoạch lô hàng | `POST /v1/cases` với `Idempotency-Key: crypto.randomUUID()` (giữ nguyên khóa khi bấm lại) |
| Danh sách hồ sơ của mình | `GET /v1/me/cases` |
| Xem giải thích và quyết định | `GET /v1/cases/{case_id}/seller-view` |

Thân `POST /v1/cases`:

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

Phản hồi `201` có `case_id`, `status`, `reason`, `version`. Mở lại sau bằng `GET /v1/me/cases`.

### 02 Lender Cockpit

| Bước | Gọi |
|---|---|
| Hàng đợi | `GET /v1/queue?status=PENDING_REVIEW&limit=50&offset=0` |
| Chi tiết hồ sơ | `GET /v1/cases/{case_id}`. Đọc `runs[-1]` cho `cashflow`, `risk` (E6), `cic` (E6a), `policy`. Tờ trình ở `documents[-1].underwriter_memo` |
| Mở bằng chứng khi bấm một câu | `GET /v1/cases/{case_id}/evidence/{evidence_id}` |
| Nhập tổng sao kê | `POST /v1/cases/{case_id}/statements` |
| Nộp file sao kê thay seller | `POST /v1/cases/{case_id}/statements/upload`, `multipart/form-data` với `expected_version`, `period_start`, `period_end`, `file` |
| Tra CIC thủ công khi Jev chặn (`runs[-1].cic.source = not_queried`) | `POST /v1/cases/{case_id}/cic-inquiry` với `expected_version`, `reason` |
| Chấp nhận, sửa số, từ chối | `POST /v1/cases/{case_id}/decisions` với `Idempotency-Key` |
| Bàn giao LOS sau khi ký | `POST /v1/cases/{case_id}/handoff` |

Mọi lệnh ghi gửi `expected_version` bằng `version` mới nhất vừa đọc. Gặp `409 CASE_VERSION_CONFLICT` thì đọc lại hồ sơ rồi cho người dùng bấm lại.

Nút "Giữ để ký số" gọi `decisions` với `ACCEPT_PROPOSAL` hoặc `ACCEPT_COUNTER`, rồi gọi `handoff`.

### 03 Policy và Eval Studio

Đăng nhập vai trò `underwriter`.

| Khối | Gọi |
|---|---|
| Bộ rule, ngưỡng, mã băm, model PD, Jev gate | `GET /v1/studio/policy` |
| Kết quả golden eval | `GET /v1/studio/eval-metrics` |

## 4. Trạng thái và lý do

| `status` | `reason` | FE hiển thị |
|---|---|---|
| `NO_LOAN_NEEDED` | `null` | Không cần vay, kèm ngưỡng payout cảnh báo |
| `AWAITING_STATEMENT` | `STATEMENT_REQUIRED` | Chờ sao kê |
| `PENDING_REVIEW` | `null` | Chờ người duyệt |
| `ESCALATED` | `HARD_POLICY_FAILURE`, `RISK_DATA_MISSING`, `MEMO_VALIDATION_FAILED` | Cần người xử lý, hiện lý do |
| `ACCEPTED`, `REJECTED` | `null` | Đã quyết |

## 5. Lỗi

Mọi lỗi nghiệp vụ có dạng `{ "detail": { "code": "...", "details": [] } }`. Lỗi kiểu dữ liệu `422` của FastAPI có `detail` là danh sách. FE nên hiện `code` và chỉ bắt riêng các mã cần xử lý:

| Mã | Việc FE làm |
|---|---|
| `401 INVALID_TOKEN`, `401 TOKEN_EXPIRED`, `401 AUTHENTICATION_REQUIRED` | Đưa về màn đăng nhập |
| `409 CASE_VERSION_CONFLICT` | Đọc lại hồ sơ |
| `422 BLOCKED_INPUT` | Báo ghi chú chứa nội dung bị chặn, không tạo hồ sơ |
| `429 TOO_MANY_ATTEMPTS` | Sai mã nhiều lần, chờ 5 phút |
| `503 AUTH_NOT_CONFIGURED`, `503 OPENROUTER_KEY_REQUIRED` | Báo lỗi cấu hình server |
