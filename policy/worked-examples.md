# Ví dụ tính tay — ezXRiverbank

Công thức khóa theo `parameters.yaml` và CashFlow Engine trong TDD. Các số dưới đây là nhãn kiểm thử. Câu "90 triệu / 12 triệu mỗi tuần" và "ngưỡng 45 triệu" trong tài liệu pitch không phải đầu ra của công thức này.

Giả định chung khi không nói khác: median payout 12 tuần = 70.000.000, tuần 0 không có inflow, stress = `int(70.000.000 × 0,7)` = 49.000.000.

## FX-B — Có gap, không cần sao kê

Đầu vào: lô trả 200.000.000 ở tuần 0, vốn 120.000.000, bán 6 tuần, xin 150.000.000, `platform_support_limit` = 120.000.000.

| Tuần | Đầu | Thu | Chi | Cuối |
|---|---:|---:|---:|---:|
| 0 | 120.000.000 | 0 | 200.000.000 | −80.000.000 |
| 1 | −80.000.000 | 70.000.000 | 0 | −10.000.000 |
| 2 | −10.000.000 | 70.000.000 | 0 | 60.000.000 |

- Gap = 80.000.000 (tuần 0). Tuần âm: 0 và 1.
- Kỳ hạn ban đầu = 2 + 2 = 4. Trích `ceil(88.000.000 / 4)` = 22.000.000.
- Trần trích = 25% × 49.000.000 = 12.250.000. 22 triệu vượt trần → kỳ hạn 6, trích 14.666.667, vẫn vượt → kỳ hạn 8.
- Trích = `ceil(88.000.000 / 8)` = 11.000.000 (22,4% payout xấu) → dừng.
- Đề xuất = 88.000.000. Không dùng 150.000.000.
- 150.000.000 < 2,5 × 120.000.000 → không đòi sao kê.
- Shop đủ tuổi, hoàn ≤ 8%, CIC nhóm 1, mọi rule cứng đạt → `PENDING_REVIEW`, `is_fast_track = true`.

## FX-A — Không thiếu tiền

Đầu vào: lô 200.000.000, vốn 120.000.000, tuần 0 trả 100.000.000, tuần 4 trả 100.000.000, bán 6 tuần, payout 70.000.000.

Tuần 0 còn 20.000.000. Tuần 4: `20.000.000 + 4 × payout − 100.000.000`. Với payout 70.000.000 mọi tuần đều không âm.

- Gap = 0. Trạng thái `NO_LOAN_NEEDED`.
- Không gọi CIC, không chấm PD, không chạy R01–R08.
- Ngưỡng cảnh báo = 20.000.000/tuần. Dưới mức đó tuần 4 âm. Đúng 20.000.000 thì số dư chạm 0, vẫn không có gap.
- FR-11 viết "45 triệu" cho kịch bản kể trên sân khấu. Fixture này không được hard-code 45.000.000.

## FX-C — Sao kê lệch

Xin 300.000.000, hạn mức sàn 120.000.000. `300.000.000 >= 2,5 × 120.000.000` → R05, `AWAITING_STATEMENT`.

Tổng dòng sàn trên sao kê 480.000.000, payout sàn cùng kỳ 280.000.000.

`mismatch = (480.000.000 − 280.000.000) / 280.000.000 = 71,4% > 15%` → R06 fail → `ESCALATED`.

Lời văn chỉ được nói số liệu không khớp, cần xác minh.

## FX-D — Lệch nằm ở tiền ngoài sàn

Dòng gắn sàn khớp trong 15%, nhưng phần xin vượt hạn mức chỉ được giải thích bằng tiền ngoài sàn. Tiền ngoài sàn không cộng vào hạn mức và không chứng minh khả năng trả. R06 fail lý do `OFF_PLATFORM_NOT_EVIDENCE` → `ESCALATED`. Không gọi đó là gian lận.

## FX-E — Rule cứng

`cic_group >= 2`, hoặc tuổi shop dưới 6 tháng, hoặc tỷ lệ hoàn 90 ngày trên 8% → `ESCALATED`. Không có trạng thái `REJECTED` cho đến khi người của ngân hàng bấm.

## FX-F — Cổng nhập

Ghi chú có lệnh ép duyệt, hoặc có thuộc tính nhạy cảm, hoặc số xin trên 1.000.000.000 → `BLOCKED_INPUT`. Không tạo nhánh thẩm định.

## FX-L — Sau giải ngân

Kỳ đang trích 12.000.000, payout tuần tụt còn 42.000.000. Trần mới = 25% × 42.000.000 = 10.500.000. R08 fail. Đề xuất giãn (trong tài liệu pitch: 8 tuần / 12 triệu → 12 tuần / 8 triệu) nằm ở `D22`, trạng thái `RESTRUCTURE_PROPOSED`. Lịch `ACTIVE` giữ nguyên đến khi có `human_decisions`.
