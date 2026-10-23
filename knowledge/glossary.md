# Thuật ngữ ezXRiverbank

Dùng đúng tên ở cột SRS. Tài liệu kiến trúc cũ có tên khác; khi viết code, prompt, và UI thì tên SRS thắng.

| Tên cũ trong sơ đồ kiến trúc | Tên khóa |
|---|---|
| No_Loan_Needed | `NO_LOAN_NEEDED` |
| Proposed | `PENDING_REVIEW` |
| Docs_Required | `AWAITING_STATEMENT` |
| Approved / Declined | `ACCEPTED` / `REJECTED` |
| Limit_Review | `RESTRUCTURE_PROPOSED` |

## Trạng thái hồ sơ

`DRAFT`, `BLOCKED_INPUT`, `ANALYZING`, `NO_LOAN_NEEDED`, `AWAITING_STATEMENT`, `VERIFYING`, `PENDING_REVIEW`, `ESCALATED`, `ACCEPTED`, `REJECTED`, `HANDOFF_COMPLETED`, `HANDOFF_ERROR`, `MONITORING_ACTIVE`, `RESTRUCTURE_PROPOSED`.

`FAST_TRACK` là cờ `is_fast_track`, không phải trạng thái.

Code hiện lưu hồ sơ ở `NO_LOAN_NEEDED`, `AWAITING_STATEMENT`, `PENDING_REVIEW`, `ESCALATED`, `ACCEPTED`, `REJECTED`. `DRAFT` và `ANALYZING` chỉ xuất hiện trong nhật ký. `BLOCKED_INPUT` trả `422`, không lưu hồ sơ. Kết quả bàn giao nằm ở `handoff.status` (`PENDING`, `SENDING`, `SUCCEEDED`, `FAILED`), không phải trạng thái hồ sơ. `VERIFYING`, `MONITORING_ACTIVE`, `RESTRUCTURE_PROPOSED`, cờ `is_fast_track` chưa có code.

## Quyết định của người

`ACCEPT_PROPOSAL`, `ACCEPT_COUNTER`, `REJECT` ở giai đoạn `UNDERWRITING_P3`. Tài liệu cũ gọi `ACCEPT_COUNTER` là `ACCEPT_MODIFIED`.

`APPROVE_RESTRUCTURE` ở giai đoạn `RESTRUCTURE_P7`. Giữ nguyên lịch thì không tạo version mới.

## Nguồn version

`SYSTEM_PROPOSED`, `HUMAN_MODIFIED`, `RESTRUCTURED_P7`. Trạng thái version: `ACTIVE` hoặc `SUPERSEDED`. Chưa có code: sửa số hiện tạo một lần chạy mới trong `runs` với `trigger = COUNTER`.

## Chứng từ

| Mã | Nghĩa |
|---|---|
| D01 | Consent dữ liệu sàn |
| D02 | CIF |
| D04 | Kết quả CIC qua cổng ngân hàng |
| D06 | PD và SHAP |
| D07 | Kết quả rule |
| D08 | Bảng đối chiếu sao kê |
| D09 | Tờ trình cho người thẩm định |
| D10 | Bản giải thích cho seller |
| D11 | Chữ ký quyết định của người duyệt |
| D22 | Đề xuất cơ cấu nợ |
| D23 | Phụ lục sau khi người duyệt chấp nhận D22 |

## Từ nghiệp vụ

| Từ | Nghĩa |
|---|---|
| Gap | Độ lớn số dư âm sâu nhất trên dòng tiền tuần, kịch bản cơ sở. Không âm thì Gap = 0 |
| Kịch bản cơ sở | Payout tuần = median lịch sử đã chọn |
| Kịch bản xấu | Payout cơ sở × `stress_factor` |
| Mức dữ liệu sàn hỗ trợ | `platform_support_limit` trong E4. Trần để đề xuất mà không cần sao kê |
| Rule cứng | Fail thì `ESCALATED`. Hệ thống không tự `REJECTED` |
| Fast-track | Gap > 0, không phải nộp sao kê, mọi rule đạt. Người duyệt bấm một nhịp |
| Versioning | Thêm bản ghi mới. Bản cũ chuyển `SUPERSEDED` |
