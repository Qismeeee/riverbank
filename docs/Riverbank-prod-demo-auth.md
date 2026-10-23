# Test underwriting trên server

Chế độ này giữ `RIVERBANK_ENV=prod`, yêu cầu Bearer token và chỉ dùng dữ liệu demo trong RAM. API không dùng RDS cho hồ sơ test; risk/CIC/LOS vẫn là adapter demo. Hồ sơ mất khi restart hoặc deploy lại. Chạy một process API như Dockerfile hiện tại để các request dùng chung repository memory.

## Dùng Swagger

1. Mở `https://13-214-87-165.sslip.io/docs` và tải lại trang sau deploy.
2. Bấm **Authorize** ở `WorkflowDemoBearer`. Dán token của `demo-b`, không thêm tiền tố `Bearer`.
3. Mở `POST /v1/cases`, nhập một `Idempotency-Key` mới. Để trống `X-Actor-ID` và `X-Actor-Role`: server lấy danh tính từ token.
4. Dán body:

```json
{
  "seller_id": "demo-b",
  "shop_id": "demo-b",
  "lot_value": 200000000,
  "cash_on_hand": 120000000,
  "supplier_schedule": [{"week": 0, "amount": 200000000}],
  "sell_weeks": 6,
  "requested_amount": 150000000,
  "note": ""
}
```

Kết quả mong đợi là HTTP 201 và `PENDING_REVIEW`. Muốn dùng `GET /v1/cases/{case_id}`, queue, quyết định hoặc handoff, bấm Authorize để đổi sang token `uw-demo`. Seller chỉ được tạo và xem seller-view của chính mình. Để test `demo-a` hoặc `demo-c`, dùng token tương ứng và đổi cả seller_id/shop_id trong body.

| HTTP / code | Ý nghĩa |
|---|---|
| 401 `INVALID_OR_EXPIRED_DEMO_TOKEN` | Thiếu, sai hoặc hết hạn token |
| 403 `SELLER_SCOPE_MISMATCH` | Token seller không khớp hồ sơ |
| 403 `UNDERWRITER_REQUIRED` | Endpoint yêu cầu token người duyệt |
| 503 `DEMO_AUTH_UNAVAILABLE` | Chưa cấp secret, secret sai cấu trúc hoặc lỗi đọc AWS |
| 503 `PRODUCTION_DEMO_REQUIRES_ISOLATED_DATA` | Cấu hình dùng DB/feature thật bị chặn |

## Cấp và xoay token

PowerShell từ repo root; file `.env.local` bị Git ignore. Lệnh không in token ra terminal và không ghi đè file đã có:

```powershell
$env:RIVERBANK_ENV = "local"
Push-Location api
.venv/Scripts/python.exe -m scripts.demo_auth generate --output ../.tmp/demo-auth/.env.local --days 7
Pop-Location
```

File chứa `tokens` để chủ repo cấp riêng cho người test, và `auth` chỉ chứa hash SHA-256, danh tính, role, thời điểm hết hạn. Không commit, gửi chat hay đính kèm file này vào PR. Không bật lưu Authorization lâu dài trên máy dùng chung.

Đưa **chỉ phần auth** vào GitHub Actions secret để pipeline cấp vào AWS Secrets Manager qua IAM role EC2:

```powershell
$bundle = Get-Content -Raw -Encoding UTF8 .tmp/demo-auth/.env.local | ConvertFrom-Json
$bundle.auth | ConvertTo-Json -Depth 10 -Compress | gh secret set WORKFLOW_DEMO_AUTH_JSON --repo Qismeeee/riverbank
```

Pipeline cần IAM cho `GetSecretValue`, `CreateSecret`, `PutSecretValue` trên `riverbank/workflow-demo-auth` (region `ap-southeast-1`). Nếu EC2 chỉ có quyền đọc, quản trị viên phải cấp secret trước và cập nhật phần `auth` tương ứng khi xoay token; provision không ghi lại nếu giá trị đã trùng.

Merge PR đã qua CI để deploy. Bước provision truyền hash qua SSH stdin; token gốc không đi qua CI hoặc EC2. Bước này cần `WORKFLOW_DEMO_AUTH_JSON` được thiết lập trước khi deploy. API tải lại secret tối đa mỗi 60 giây; lỗi đọc hoặc cấu trúc sai sẽ từ chối xác thực. Token hết hạn được từ chối ngay tại thời điểm hết hạn.

Để xoay token, sinh một file `.env.local` trong thư mục riêng khác, cập nhật GitHub secret bằng phần `auth` mới rồi chạy lại deploy. Danh sách mới thay thế danh sách token cũ. Để thu hồi sớm, cập nhật cả GitHub secret và AWS secret; nếu chỉ sửa AWS thì lần deploy sau sẽ khôi phục danh sách từ GitHub. Để tắt demo, đổi `RIVERBANK_WORKFLOW_MODE=disabled` trong `infra/.env.prod` qua PR.

Local mặc định vẫn hỗ trợ header demo với `RIVERBANK_WORKFLOW_AUTH=local_headers`; server prod bắt buộc `bearer`. Đây là cơ chế cấp quyền cho bộ demo, không phải tích hợp danh tính khách hàng thật.
