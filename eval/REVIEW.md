# Human review — 26/10/2026

Commit: `97d7fbe7a6afa155ef55274b8fea4b2f0d21d7eb`

Model sinh: `gpt-4.1-mini-2025-04-14`

Nguồn: `live-evaluation-review.json`. Review 10 case đầu có tài liệu, mỗi case dùng tài liệu cuối sau khi workflow hoàn tất.

## Kết quả

| Case | Engine/status | Underwriter memo | Seller explanation | Kết luận |
|---|---|---|---|---|
| FX-A | Đúng | Đúng evidence | Đúng | Pass |
| FX-B | Đúng | Đúng evidence | Gán đề xuất 90 triệu thành “Mình muốn vay”; input yêu cầu 150 triệu | Fail |
| FX-C-awaiting | Đúng | Đúng evidence | Gán đề xuất của engine thành “Tôi đề xuất vay” | Fail |
| FX-C-mismatch | Đúng | Đúng evidence và trạng thái | Gán đề xuất của engine thành “Tôi đề xuất vay” | Fail |
| FX-C-matched | Đúng | Đúng evidence và trạng thái | Gán đề xuất của engine thành “Mình muốn vay” | Fail |
| FX-C-boundary | Đúng | Đúng evidence | Gán đề xuất của engine thành “Mình muốn vay” | Fail |
| FX-E-cic_group_3 | Đúng | Đúng evidence và trạng thái | Gán đề xuất của engine thành “Shop muốn vay” | Fail |
| FX-E-cic_group_2 | Đúng | Đúng evidence | Gán đề xuất của engine thành “Mình muốn vay” | Fail |
| FX-E-shop_age | Đúng | Đúng evidence và trạng thái | Gán đề xuất của engine thành “Tôi muốn vay” | Fail |
| FX-E-return_rate_soft | Đúng | Đúng evidence | Gán đề xuất của engine thành “Mình đề xuất vay” | Fail |

Human faithfulness: **1/10 case pass** theo tiêu chí mọi câu phải diễn giải đúng vai trò của evidence.

Toàn bộ 17/17 tài liệu có slot `seller_explanation.proposal` mắc cùng nhóm lỗi. Giá trị số vẫn đúng, nhưng chủ thể và ý nghĩa sai: `amount_proposed` là số do `CashFlowEngine` đề xuất, không phải `requested_amount` do seller nhập.

Seller explanation của case vay hiện có 4 câu (`source`, `gap`, `proposal`, `deduction`), trong khi skill `memo-evidence` quy định bản giải thích ngắn 3 câu. Đây là lệch cấu trúc độc lập với lỗi chủ thể.

## Đối chiếu các lớp chấm

- Validator Python: 18/18 tài liệu LLM pass.
- Jev judge: 18/18 tài liệu pass bốn câu hỏi hiện tại.
- Human review: 1/10 case pass.

Khoảng cách này cho thấy bộ validator/judge hiện tại chưa kiểm quan hệ `claim -> vai trò ngữ nghĩa`. Metric Jev không có câu hỏi về việc gán nhầm `amount_proposed` thành mong muốn của seller.

## Cách sửa nên áp dụng

Code render nguyên văn các câu chứa số và chủ thể: gap, đề xuất, kỳ hạn, kỳ trích, trạng thái, phí/lãi. Nếu vẫn dùng LLM, chỉ cho viết phần giải thích không chứa facts khóa hoặc buộc output chọn từ các semantic frame do code định nghĩa.

Thêm adversarial golden cho:

- tráo số tiền và kỳ hạn;
- đúng số nhưng sai chủ thể (`engine đề xuất` thành `seller muốn`);
- số viết bằng chữ;
- bịa phí/lãi không có số dạng chữ số;
- diễn đạt tương đương “đã chấp thuận” khi còn chờ duyệt.

Runner release phải fail nếu human sample hoặc semantic-frame validator fail; Jev là lớp chấm bổ sung, không thay validator code.

## Sau khi sửa

Nhánh `fix/memo-semantic-eval-gate` khóa toàn bộ câu chứa fact bằng code và gắn semantic frame gồm actor, relation, roles, units và audience. Seller explanation còn đúng 3 câu; câu đề xuất luôn ghi rõ "Hệ thống đề xuất", không gán thành nhu cầu của seller. Khi chưa có prose slot thuần, cấu hình writer `llm` không gọi model.

Đã thêm 5 adversarial golden: tráo tiền/kỳ hạn, sai chủ thể, số bằng chữ, phí bịa và diễn đạt "đã chấp thuận". Release gate chạy đủ workflow và submit statement, so toàn bộ expected field, đồng thời trả exit code khác 0 khi mismatch, lỗi validator hoặc fallback.

Kết quả xác nhận:

- golden release gate: 20/20 pass, 0 fallback, 0 LLM call;
- full offline suite: 343 pass, 11 Postgres test được chạy riêng sau khi bật Docker và đều pass;
- Postgres integration: 2/2 ETL và 9/9 workflow pass;
- diff không chứa key hoặc `.env`.
