# API còn cần backend chỉnh

Frontend đã gọi các đầu dưới đây. Nộp file sao kê không cần đầu mới. Hàng «Đã thẩm định» cần backend khớp tài liệu và hai repository.

## 1. Nộp file sao kê — đã có, không thêm đầu

`POST /v1/cases/{case_id}/statements/upload` như mục 4.8a trong `Riverbank-API.md`.

Seller của hồ sơ và người thẩm định đều gọi đầu này khi hồ sơ `AWAITING_STATEMENT`, hoặc `ESCALATED` mà lần chạy cuối có R05 fail.

Thân `multipart/form-data`:

| Trường | Kiểu |
|---|---|
| `expected_version` | int, bằng `version` đang đọc |
| `period_start`, `period_end` | date, thứ Hai, `end` sau `start` |
| `file` | CSV, PDF, PNG, JPEG, tối đa 10 MB |

Phản hồi `200` là `CaseSummary`. `version` phải bằng `expected_version + 1`. Lần ghi này chạy lại thẩm định (`trigger = STATEMENT`), không tạo `case_id` mới.

Nhập tổng tay vẫn là `POST /v1/cases/{case_id}/statements` và cũng tăng `version` thêm 1.

Lỗi FE đã hiện: `STATEMENT_NOT_REQUESTED`, `CASE_VERSION_CONFLICT`, `INVALID_STATEMENT_PERIOD`, `STATEMENT_PERIOD_NOT_COVERED`, `STATEMENT_TOO_LARGE`, `STATEMENT_FORMAT_UNSUPPORTED`, `STATEMENT_EMPTY`, `STATEMENT_UNREADABLE`, `STATEMENT_LINES_UNREADABLE`, `STATEMENT_TOO_MANY_PAGES`, `STATEMENT_OCR_UNAVAILABLE`, `STATEMENT_CLASSIFIER_UNAVAILABLE`, `STATEMENT_READER_UNAVAILABLE`.

## 2. Hàng đợi «Đã thẩm định» — cần chỉnh

FE gọi song song:

```
GET /v1/queue?status=ACCEPTED&limit=100&offset=0
GET /v1/queue?status=REJECTED&limit=100&offset=0
```

Hai mã này là hồ sơ đã có quyết định người thẩm định. Không thêm trạng thái mới.

`GET /v1/queue` không có `status` giữ nguyên: chỉ `PENDING_REVIEW`, `ESCALATED`, `NO_LOAN_NEEDED`, `AWAITING_STATEMENT`.

Khi `status` là `ACCEPTED` hoặc `REJECTED`:

- Trả đúng các hồ sơ ở trạng thái đó.
- Sắp `created_at desc, case_id desc`.
- `limit` 1–100, `offset` ≥ 0, cùng vỏ `{ items, limit, offset }`.

Việc backend phải làm:

1. `MemoryCaseRepository.list_queue` hiện loại `ACCEPTED` và `REJECTED` cả khi client truyền đúng `status`. Bỏ điều kiện «chỉ tập đang mở» khi `status` được truyền.
2. `PostgresCaseRepository.list_queue` đã trả theo `status` truyền vào. Giữ hành vi đó và thêm test cho cả hai repository.
3. Sửa câu trong `Riverbank-API.md` mục 4.5: hàng mặc định không chứa hồ sơ đã quyết; `status=ACCEPTED` và `status=REJECTED` là lối đọc hồ sơ đã thẩm định. Thứ tự tài liệu đang ghi tăng dần, code đang giảm dần. Lấy thứ tự code.

Không gộp hai trạng thái thành một mã `DECIDED` trên API. FE tự ghép hai danh sách.
