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

- Chỉ tài khoản trong danh bạ `api/data/users.json` mới đăng nhập được. Sai `subject`, sai `role` hay sai mã đều trả `401 INVALID_CREDENTIALS`.
- Phản hồi đăng nhập và `GET /v1/auth/me` có `full_name` và `display_name`. Hiển thị tên này trên header, không hiển thị `subject`.
- `passcode` là mã truy cập chung, chủ dự án đưa riêng. Không ghi mã vào code FE.

| `subject` | `role` | Họ tên | `display_name` | Ghi chú |
|---|---|---|---|---|
| `thuha.closet` | seller | Nguyễn Thu Hà | Hộ kinh doanh Thu Hà Closet | Payout 40 triệu mỗi tuần |
| `minhchau.shop` | seller | Lê Minh Châu | Hộ kinh doanh Minh Châu Shop | Case B: thiếu 80 triệu, đề xuất 90 triệu, 8 tuần |
| `ducthinh.tech` | seller | Phan Đức Thịnh | Hộ kinh doanh Đức Thịnh Tech | Sàn chỉ xác nhận 30 triệu, xin cao phải nộp sao kê |
| `ngocvy.boutique` | seller | Hồ Ngọc Vy | Hộ kinh doanh Ngọc Vy Boutique | Cùng số với `minhchau.shop` nhưng shop rủi ro hơn, PD vượt trần, R10 fail, sang `ESCALATED` |
| `maianh.fashion` | seller | Trần Thị Mai Anh | Hộ kinh doanh Mai Anh Fashion | Hồ sơ HS01 |
| `phucan.beauty` | seller | Lê Hoàng Phúc | Hộ kinh doanh Phúc An Beauty | Hồ sơ HS02 |
| `khang.phukien` | seller | Phạm Minh Khang | Hộ kinh doanh Khang Phụ Kiện | Hồ sơ HS03 |
| `tung.giadung` | seller | Võ Thanh Tùng | Hộ kinh doanh Tùng Gia Dụng | Hồ sơ HS04 |
| `han.store` | seller | Đỗ Ngọc Hân | Hộ kinh doanh Hân Store | Hồ sơ HS05 |
| `hau.mart` | seller | Nguyễn Văn Hậu | Hộ kinh doanh Hậu Mart | Hồ sơ HS06 |
| `tran.ngoclan` | underwriter | Trần Ngọc Lan | Chuyên viên thẩm định | |
| `nguyen.ducminh` | underwriter | Nguyễn Đức Minh | Chuyên viên thẩm định | |
| `le.thuthao` | underwriter | Lê Thu Thảo | Trưởng phòng thẩm định | |

Mã cũ `demo-a` đến `demo-j` và `uw-01` không còn đăng nhập được.
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
| Chat nhập kế hoạch | `POST /v1/copilot/extract-plan`. Hiện `reply` trong khung chat. Lượt sau gửi `known = draft` của lượt trước. `ready = true` thì bật nút Gửi, gửi nguyên `draft` vào `POST /v1/cases` |
| Hỏi về hồ sơ của mình | Mở khung chat thì gọi `GET /v1/cases/{case_id}/copilot/history` để hiện lại các lượt cũ. Hỏi bằng `POST /v1/cases/{case_id}/copilot/ask` chỉ với `question`, server tự nhớ lịch sử. Hiện `answer`. `source = "template"` là câu mẫu khi LLM lỗi, vẫn hiện bình thường |
| Nộp sao kê | Nên dùng `POST /v1/cases/{case_id}/statements/upload-jobs`: nhận `202` và `job_id`, gọi `GET /v1/jobs/{job_id}` mỗi 2 giây đến khi `SUCCEEDED` (tải lại hồ sơ) hoặc `FAILED` (hiện `error.code`). Bản chờ trực tiếp `POST .../statements/upload` vẫn dùng được cho CSV, Excel. Nhận CSV, `.xlsx`, `.xls`, PDF, ảnh. `accept=".csv,.xlsx,.xls,.pdf,.png,.jpg,.jpeg,.webp,.heic"` |

Thân `POST /v1/cases`:

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

Phản hồi `201` có `case_id`, `status`, `reason`, `version`. Mở lại sau bằng `GET /v1/me/cases`.

### 02 Lender Cockpit

| Bước | Gọi |
|---|---|
| Hàng đợi | `GET /v1/queue?status=PENDING_REVIEW&limit=50&offset=0` |
| Chi tiết hồ sơ | `GET /v1/cases/{case_id}`. Đọc `runs[-1]` cho `cashflow`, `risk` (E6), `cic` (E6a), `policy`. Tờ trình ở `documents[-1].underwriter_memo` |
| Mở bằng chứng khi bấm một câu | `GET /v1/cases/{case_id}/evidence/{evidence_id}` |
| Nhập tổng sao kê | `POST /v1/cases/{case_id}/statements` |
| Nộp file sao kê thay seller | `POST /v1/cases/{case_id}/statements/upload`, `multipart/form-data` với `expected_version`, `period_start`, `period_end`, `file` |
| Tra CIC thủ công khi Jev chặn (`runs[-1].cic.source` là `not_queried`, hoặc `deferred_until_statement` khi hồ sơ cần sao kê chưa nộp) | `POST /v1/cases/{case_id}/cic-inquiry` với `expected_version`, `reason` |
| Chấp nhận, sửa số, từ chối | `POST /v1/cases/{case_id}/decisions` với `Idempotency-Key` |

### Phân trang danh sách hồ sơ

`GET /v1/queue` và `GET /v1/me/cases` mặc định 50 hồ sơ/trang, không còn trần
100 hồ sơ/trang. `limit` là số nguyên dương; có thể yêu cầu trang lớn hơn khi cần,
nhưng nên tải từng trang để giao diện phản hồi nhanh. Tổng số hồ sơ duyệt qua không bị giới hạn.

FE dạng bảng dùng `?page_size=50&page_index=0`, trang tiếp theo là `page_index=1`.
`page_index` bắt đầu từ 0; response có `page_size`, `page_index`, `next_page_index`
và `has_more`. Không có `total`/`total_pages` vì API không chạy COUNT toàn bộ ở mỗi trang.

Response vẫn giữ `items`, `limit`, `offset` và có `next_cursor`, `next_offset`.
Danh sách lớn nên gọi trang đầu với `?page_size=50`, sau đó truyền nguyên `next_cursor` vào
`?page_size=50&cursor=...` cho tới khi `has_more=false`. Cursor dùng cặp thời gian tạo/ID,
có index hỗ trợ và không cần đếm toàn bộ bảng hoặc bỏ qua offset lớn ở từng lần gọi.

Thứ tự giữ nguyên: queue đang xử lý cũ trước, hồ sơ đã quyết định và danh sách seller
mới trước; ID phân biệt các hồ sơ cùng thời điểm. Đây là danh sách cập nhật trực tiếp,
không phải snapshot: hồ sơ đổi trạng thái có thể rời/đi vào bộ lọc trong lúc duyệt.
Đổi seller hoặc bộ lọc `status` thì bắt đầu lại trang đầu, không tái dùng cursor cũ.
Cursor hỏng/sai phạm vi trả `422 INVALID_CURSOR`; gửi cursor với offset khác 0 trả
`422 INVALID_PAGINATION`. Không gửi `page_index` cùng cursor hoặc offset; không gửi
`page_size` và `limit` khác nhau. Auth và scope seller vẫn được kiểm riêng ở backend.

Client cũ vẫn dùng `offset` và `next_offset`; khi hết dữ liệu, hai trường next là null.
Ở chế độ cursor, `page_index`, `next_page_index`, `next_offset` luôn null. Với offset
không trùng đầu trang, `page_index`/`next_page_index` cũng null. Không dùng cursor để
nhảy tới số trang tùy ý.
| Bàn giao LOS sau khi ký | `POST /v1/cases/{case_id}/handoff` |

Mọi lệnh ghi gửi `expected_version` bằng `version` mới nhất vừa đọc. Gặp `409 CASE_VERSION_CONFLICT` thì đọc lại hồ sơ rồi cho người dùng bấm lại.

Nút "Giữ để ký số" gọi `decisions` với `ACCEPT_PROPOSAL` hoặc `ACCEPT_COUNTER`, rồi gọi `handoff`.

### 03 Policy và Eval Studio

Đăng nhập vai trò `underwriter`.

| Khối | Gọi |
|---|---|
| Bộ rule, ngưỡng, mã băm, model PD, Jev gate | `GET /v1/studio/policy` |
| Kết quả golden eval | `GET /v1/studio/eval-metrics` |
| Lưu lượng, thời gian, chi phí | `GET /v1/studio/traffic`, `Riverbank-API.md` mục 6.8 |

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
| `429 RATE_LIMITED` | Gọi quá nhanh. Đọc header `Retry-After` (giây), báo "thử lại sau N giây", không tự gọi lại liên tục |
| `503 AUTH_NOT_CONFIGURED`, `503 OPENROUTER_KEY_REQUIRED` | Báo lỗi cấu hình server |

Mặc định tạo hồ sơ, upload sao kê và Copilot đều giới hạn 60 request trong 60 giây,
đếm riêng theo người gọi, nhóm API và tiến trình Uvicorn. Đây chưa phải quota dùng
chung giữa nhiều worker. Giới hạn đăng nhập sai và quota nhà cung cấp model là riêng.
