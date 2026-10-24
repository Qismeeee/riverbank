# Chính sách tín dụng Riverbank (bản demo)

Nguồn: docs/Riverbank-BRD.md mục BL, docs/Riverbank-SRS.md FR-17 đến FR-20.
Mỗi rule dưới đây có một hàm cùng mã trong `rules.py` và một test trong `test_rules.py`.
Rule cứng (hard) fail thì hồ sơ sang Escalated, người duyệt không ghi đè được.
Rule mềm (soft) fail thì ghi cảnh báo vào memo, người duyệt quyết.
Thiếu dữ liệu để chạy rule cứng thì coi như missing_data, cũng sang Escalated.

Ngưỡng ghi (*) là giá trị minh họa cho hackathon, team chốt lại với ngân hàng.

| Rule | Loại | Nội dung | Ngưỡng | Nguồn |
|---|---|---|---|---|
| R01 | hard | Số tiền không vượt hạn mức sản phẩm | 300 triệu (*) | BL-01 |
| R02 | hard | Shop hoạt động đủ lâu | 6 tháng (*) | BL-04 |
| R03 | soft | Tỷ lệ hoàn hàng 12 tuần | 15% (*) | BL-06 |
| R04 | hard | Kỳ hạn trong khung sản phẩm | 2 đến 26 tuần (*) | BL-03 |
| R05 | hard | Xin vượt 2,5 lần mức dữ liệu sàn hỗ trợ thì phải có chứng từ đã xác minh | 2,5 lần | BL-05 |
| R06 | hard | Nhóm nợ CIC | nhóm 1 hoặc 2 (*) | BR-21 |
| R07 | soft | Dư nợ hiện hữu tại các TCTD so với payout tháng | 3 lần (*) | BR-21 |
| R08 | hard | Kỳ trích không vượt 25% payout xấu | 25% | BL-07 |
| R09 | hard | Vay điện tử cho cá nhân không vượt 100 triệu dư nợ | 100 triệu | BL-09, TT 06/2023 |
| R10 | hard | PD của model không vượt trần | 3.000 bps (*) | BR-11 |

Thứ tự chạy: R09, R01, R04, R08 (số tiền và lịch) rồi R02, R03, R05 (hồ sơ) rồi R06, R07 (CIC) rồi R10 (PD).
Duyệt số khác (FR-32) chấm lại PD và chạy lại toàn bộ bảng này trên bộ số mới.

R05 dùng `requested_amount` khi caller cung cấp; caller cũ chưa có trường này
tiếp tục dùng `amount`. Luồng hồ sơ truyền số seller xin; counter-offer truyền
`max(requested_amount, final_amount)`. R01/R04/R08/R09 vẫn kiểm tra điều kiện đề
xuất/cuối cùng. Thiếu chứng từ riêng R05 thì chờ sao kê, không cho duyệt; nếu có
hard fail khác thì Escalated. Sao kê được bổ sung sẽ tạo run mới và chạy lại rule.
