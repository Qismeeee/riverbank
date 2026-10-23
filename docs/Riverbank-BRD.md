# BRD — Riverbank

| | |
|---|---|
| Sản phẩm | Riverbank |
| Loại tài liệu | Business Requirements Document |
| Phiên bản | 1.1 |
| Trạng thái | Bản làm việc cho team |
| Đối tượng đọc | Nghiệp vụ, presenter, dev |
| Nguồn | `riverbank-architecture.md`, quy trình tín dụng ngân hàng |

**Thay đổi 1.1 so với 1.0:** ghi rule mềm và rule cứng theo code ở case E, ghi case D chưa có code. Số và rule chạy theo `api/` (xem `docs/Riverbank-spec-vs-code.md`).

**Thay đổi 1.0 so với 0.9:** đổi tên sản phẩm thành Riverbank; chốt mô hình tích hợp A (Riverbank là LOS, mục 6); sửa số ví dụ case A và B cho khớp công thức (mục 9); thêm quy tắc làm tròn BL-02, trần cho vay điện tử BL-09, hiệu lực quyết định BL-10, xử lý trích thiếu BL-11; thêm BR-21 về CIC; sửa case D.

Tài liệu này ghi **việc nghiệp vụ cần hệ thống làm**. Cách làm (agent, model, thuật toán) nằm ở tài liệu kiến trúc và SRS.

---

## 1. Tóm tắt

Seller trên sàn thiếu tiền khi phải trả xưởng trước, trong khi payout về sau. Đó là khoảng thiếu vốn lưu động, không phải “shop bán yếu”.

Riverbank trả lời ba câu cho một lô hàng: có thiếu thật không, thiếu bao nhiêu và trong bao lâu, trả dần từ payout thì có kham nổi không. Kết quả là một hồ sơ quyết định có bằng chứng, để người của ngân hàng duyệt. Ngân hàng giữ vốn, hợp đồng, giải ngân và sổ cái.

Đối thủ lớn nhất của một khoản vay là seller không cần vay. Nếu dòng tiền tuần không âm, hệ thống kết luận không đề xuất vay.

## 2. Bài toán

Seller không thiếu tiền vì bán ít. Seller thiếu tiền vì lịch chi và lịch thu lệch nhau.

Ví dụ: lô 200 triệu, seller có 120 triệu, xưởng bắt trả hết trước, hàng bán khoảng 6 tuần. Thiếu 80 triệu trong những tuần đó. Nếu xưởng cho cọc 50% và trả nốt sau, khoảng thiếu có thể biến mất.

Ngân hàng hiện trả lời chậm vì không thấy dữ liệu bán hàng trên sàn. Seller phải tự mang ảnh chụp doanh thu và sao kê. Khoản vay, khi được duyệt, thường là trả góp nhiều tháng, không khớp số tuần seller thực sự thiếu tiền.

Sản phẩm vay trên sàn hiện tại (SEasy Cho Vay Người Bán) đưa đơn vào Kênh Người Bán, nhưng vẫn là khoản hạn mức, kỳ hạn 3–24 tháng, trả góp hàng tháng. Bên cho vay là tổ chức tín dụng. Sàn không tính khoảng thiếu của từng lô.

## 3. Mục tiêu

| Mã | Mục tiêu | Thước đo ở bản demo |
|---|---|---|
| O1 | Chỉ đề xuất vay khi có khoảng thiếu thật | Case không có tuần âm thì kết luận không cần vay |
| O2 | Đề xuất theo dòng tiền, không theo số seller xin | Số tiền = gap cộng đệm, trong trần đã chốt |
| O3 | Người thẩm định tin được hồ sơ | 100% câu có số trong memo trỏ về bằng chứng |
| O4 | Ít giấy tờ khi dữ liệu sàn đủ | Sao kê chỉ khi số xin vượt mức dữ liệu sàn hỗ trợ |
| O5 | Quyết định cuối thuộc người của ngân hàng | Không có trạng thái duyệt mà không có người bấm |

## 4. Bên liên quan

| Bên | Cần gì từ sản phẩm |
|---|---|
| Seller | Biết có cần vay không; nếu cần thì vay bao nhiêu, bao lâu, mỗi tuần bị trừ bao nhiêu, tổng phải trả gồm những gì |
| Người thẩm định ngân hàng | Tờ trình đọc được, bấm vào bằng chứng thấy số gốc, biết rule nào pass hoặc fail |
| Ngân hàng (chủ vốn) | Khẩu vị rủi ro của mình được chạy đúng; người của mình chịu trách nhiệm quyết định |
| Shopee | Dữ liệu sàn được dùng đúng consent; payout là nguồn trả nợ, không phải bên cho vay |
| Team sản phẩm | Phạm vi lõi làm xong trước các bước hợp đồng và giải ngân |

## 5. Phạm vi

### Trong phạm vi

Phần lõi là thẩm định một kế hoạch lô hàng và lập hồ sơ quyết định:

1. Nhận kế hoạch lô hàng.
2. Lấy dữ liệu bán hàng và payout trên sàn.
3. Dựng dòng tiền tuần, tính gap.
4. Nếu có gap: chấm rủi ro, chạy chính sách, viết tờ trình và bản giải thích cho seller.
5. Nếu số xin vượt mức dữ liệu sàn hỗ trợ: yêu cầu sao kê và đối chiếu với payout.
6. Đưa hồ sơ cho người thẩm định. Ghi nhận duyệt, từ chối, hoặc chuyển người.
7. Gói hồ sơ này là đầu ra bàn giao cho LOS của ngân hàng.

### Kế thừa ngân hàng, có nhắc trong luồng, không đào sâu

Các bước sau vẫn tồn tại trong pipeline tín dụng. Ngân hàng đã có hệ thống cho chúng. Team có thể nối một đầu ra tối thiểu, không biến chúng thành sản phẩm:

| Bước pipeline | Ai giữ | Riverbank làm đến đâu |
|---|---|---|
| eKYC, CIF, consent | Ngân hàng | Giả định seller đã được định danh và đã đồng ý chia sẻ dữ liệu |
| CIC | Ngân hàng | Gọi qua API của ngân hàng, chỉ khi đã có gap; không tự kết nối CIC. Kết quả là đầu vào cho rule |
| Phê duyệt pháp lý, thẩm quyền, hợp đồng, khế ước, ủy nhiệm chi | Ngân hàng | Mô hình A: Riverbank sinh chứng từ từ mẫu ngân hàng và gọi API core. Bản demo lõi dừng ở quyết định và gói bàn giao, các bước sau mô phỏng |
| Giải ngân, hạch toán, lãi, phân nhóm nợ, báo cáo CIC | Core banking | Không hạch toán, không chuyển tiền |
| Trích payout để trả nợ | Shopee và core | Ghi trong đề xuất lịch trừ; không vận hành lệnh trích |
| Sau giải ngân: re-score, giãn hạn | Ngân hàng | Ngoài phạm vi bản này. Hướng mở rộng, làm khi phần lõi đã xong |

### Ngoài phạm vi

- Cho vay tiêu dùng, hạn mức quay vòng nhiều lần rút.
- Tự giải ngân, tự từ chối, tự cơ cấu nợ.
- Đọc dữ liệu seller chưa consent.
- Kết luận gian lận. Hệ thống chỉ nêu lệch số và chuyển người.

## 6. Vị trí trên pipeline ngân hàng

Pipeline chuẩn vẫn là: tiếp nhận, thẩm định, phê duyệt, soạn thảo, giải ngân, thu nợ, tất toán. Riverbank là **LOS của sản phẩm này** (mô hình A trong tài liệu luồng nghiệp vụ): giữ khúc thẩm định, lập tờ trình và màn hình phê duyệt cho nhân sự ngân hàng, sinh chứng từ theo mẫu ngân hàng và gửi lệnh có xác thực sang core. Vốn, quyết định tín dụng, sổ cái, CIC thuộc ngân hàng. Bản demo hackathon làm thật khúc tiếp nhận, thẩm định, phê duyệt và gói bàn giao; các khúc sau mô phỏng. Mô hình B (Riverbank chỉ là underwriting engine, đẩy gói sang LOS ngân hàng) là lộ trình, gói bàn giao BR-19 được giữ để chuyển sang B không phải làm lại.

```text
Seller khai kế hoạch
        |
        v
[ Riverbank: dữ liệu sàn, dòng tiền, gap, rủi ro, rule, memo ]
        |
        +-- không có gap --> không đề xuất vay
        |
        +-- có gap, trong mức dữ liệu --> hồ sơ đề xuất
        |
        +-- vượt mức hoặc fail rule cứng --> hồ sơ chuyển người
        |
        v
Người thẩm định xem hồ sơ, bấm quyết định
        |
        v
[ LOS ngân hàng: hợp đồng, khế ước, ủy nhiệm chi ]
        |
        v
[ Core: giải ngân, thu nợ, quá hạn, tất toán ]
```

Năm chứng từ hay gặp trên pipeline, để không nhầm với tờ trình:

| Chứng từ | Khúc nào | Ai lập trong phạm vi này |
|---|---|---|
| Tờ trình | Thẩm định | Riverbank |
| Quyết định phê duyệt | Phê duyệt | Người của ngân hàng, bấm trên màn hình của Riverbank. Riverbank ghi nhận và kiểm khớp rule |
| Hợp đồng tín dụng | Soạn thảo | Ngân hàng |
| Khế ước nhận nợ | Giải ngân | Ngân hàng. Đây là mốc phát sinh nợ và tính lãi |
| Ủy nhiệm chi | Giải ngân | Ngân hàng. Lệnh chuyển tiền giải ngân đến xưởng hoặc tài khoản seller |

## 7. Quy trình đích của phần lõi

1. Seller nhập giá trị lô, tiền đang có, điều khoản xưởng, số tuần dự kiến bán hết, số muốn vay.
2. Hệ thống chặn nội dung không được đưa vào thẩm định: dữ liệu cá nhân thừa, lệnh can thiệp prompt, thuộc tính nhạy cảm, số tiền hoặc kỳ hạn vượt trần cứng.
3. Hệ thống lấy lịch sử đơn và payout. Payout các tuần tới trong phương án là mức dự kiến từ lịch sử, không phải số seller tự khai.
4. Hệ thống dựng số dư tiền mặt từng tuần ở hai kịch bản: cơ sở và xấu. Số dư cuối tuần = số dư đầu tuần + payout dự kiến − tiền phải trả xưởng trong tuần.
5. Gap là số dư âm sâu nhất ở kịch bản cơ sở. Không có tuần âm thì gap = 0, dừng, giải thích vì sao không cần vay và ngưỡng payout mà dưới đó kế hoạch sẽ thiếu tiền.
6. Có gap thì chấm xác suất không trả được, giải thích các yếu tố chính, tra CIC qua ngân hàng, rồi chạy từng rule chính sách. Không có gap thì không tra CIC, không tốn phí tra cứu.
7. Fail rule cứng thì chuyển người, không tự quyết.
8. Số xin vượt mức dữ liệu sàn hỗ trợ thì yêu cầu sao kê. Đối chiếu dòng tiền ghi là từ sàn với payout thực. Lệch quá ngưỡng thì chuyển người. Memo không dùng từ gian lận.
9. Tờ trình và bản cho seller chỉ dùng số đã có bằng chứng.
10. Người thẩm định xem, bấm duyệt đề xuất, duyệt số khác, hoặc từ chối. Duyệt số khác thì hệ thống tính lại lịch trừ và chạy lại toàn bộ rule trên số mới trước khi ghi nhận. Hệ thống ghi ai bấm, lúc nào, dựa trên bằng chứng nào, rồi đóng gói hồ sơ bàn giao.

## 8. Yêu cầu nghiệp vụ

| Mã | Yêu cầu | Ưu tiên |
|---|---|---|
| BR-01 | Seller nộp một kế hoạch cho đúng một lô hàng, gồm giá trị lô, vốn đang có, điều khoản thanh toán với xưởng, số tuần bán hết và số muốn vay | Bắt buộc |
| BR-02 | Hệ thống từ chối đưa vào thẩm định nội dung có dữ liệu cá nhân thừa, lệnh can thiệp, thuộc tính nhạy cảm, hoặc số vượt trần cứng | Bắt buộc |
| BR-03 | Thẩm định dùng dữ liệu bán hàng và payout trên sàn của đúng seller đã consent, không dùng ảnh chụp seller tự tải khi dữ liệu sàn đã đủ | Bắt buộc |
| BR-04 | Payout tương lai trong phương án lấy từ lịch sử payout của shop | Bắt buộc |
| BR-05 | Hệ thống tính số dư tiền mặt lũy kế theo tuần, với kịch bản cơ sở và kịch bản xấu | Bắt buộc |
| BR-06 | Gap bằng số dư âm sâu nhất của kịch bản cơ sở. Không tuần nào âm thì kết luận không đề xuất vay | Bắt buộc |
| BR-07 | Khi không đề xuất vay, seller nhận được lý do và ngưỡng payout mà dưới đó khoảng thiếu xuất hiện trở lại | Bắt buộc |
| BR-08 | Khi có gap, số tiền đề xuất bằng gap cộng một khoản đệm, không vượt trần đệm, và không lấy số seller xin làm số đề xuất | Bắt buộc |
| BR-09 | Kỳ hạn đề xuất bằng số tuần dòng tiền âm cộng số tuần đệm | Bắt buộc |
| BR-10 | Khoản trừ mỗi tuần không vượt một tỷ lệ trần của payout kịch bản xấu. Vượt thì kéo dài kỳ hạn | Bắt buộc |
| BR-11 | Hồ sơ có gap được chấm xác suất không trả được, kèm các yếu tố giải thích chính | Bắt buộc |
| BR-12 | Hồ sơ được kiểm tra từng rule trong chính sách ngân hàng đang hiệu lực. Kết quả rule không bị sửa bởi lời văn | Bắt buộc |
| BR-13 | Fail rule cứng thì hồ sơ chuyển người, không thành đề xuất duyệt thẳng | Bắt buộc |
| BR-14 | Sao kê chỉ bị yêu cầu khi số xin vượt mức mà dữ liệu sàn hỗ trợ | Bắt buộc |
| BR-15 | Sao kê được đối chiếu với payout sàn. Lệch quá ngưỡng thì chuyển người. Văn bản không kết luận gian lận | Bắt buộc |
| BR-16 | Tờ trình cho thẩm định viên và bản giải thích cho seller cùng ra từ một bộ số. Bản cho seller nói tổng phải trả, lãi, phí và lịch trừ bằng tiếng thường | Bắt buộc |
| BR-17 | Mọi câu có số trong tờ trình trỏ về bằng chứng là bảng số gốc | Bắt buộc |
| BR-18 | Người của ngân hàng là người ra quyết định cuối trên hồ sơ. Hệ thống ghi nhận quyết định đó | Bắt buộc |
| BR-19 | Sau khi có quyết định, hệ thống bàn giao gói hồ sơ để LOS ngân hàng tiếp tục soạn thảo và giải ngân | Bắt buộc |
| BR-20 | Mọi lần đổi trạng thái hồ sơ ghi ai, lúc nào, dựa trên bằng chứng nào | Bắt buộc |
| BR-21 | Kết quả CIC được lấy qua ngân hàng chỉ khi hồ sơ có gap, gắn vào hồ sơ như một bằng chứng và là đầu vào của rule. Không có kết quả thì rule liên quan ghi thiếu dữ liệu và hồ sơ chuyển người | Bắt buộc |

## 9. Quy tắc nghiệp vụ

Các số dưới đây là quy tắc của bản demo, lấy từ tài liệu kiến trúc. Chính sách ngân hàng thật có thể thay chúng. Hệ thống phải chạy theo bộ quy tắc đang được ngân hàng duyệt, không gắn cứng trong câu chữ của tờ trình.

| Mã | Quy tắc |
|---|---|
| BL-01 | Kịch bản xấu = payout kịch bản cơ sở × 0,7 |
| BL-02 | Số tiền đề xuất = gap × 1,1, làm tròn lên bội 5 triệu, và không vượt gap × 1,15 |
| BL-03 | Kỳ hạn cơ sở = số tuần âm liên tục + 2 tuần đệm. Kỳ hạn thực tế thường dài hơn do BL-04 |
| BL-04 | Khoản trừ tuần vượt 25% payout kịch bản xấu thì cộng thêm 2 tuần và tính lại |
| BL-05 | Xin vượt 2,5 lần mức dữ liệu sàn hỗ trợ thì phải có chứng từ (rule R05 trong ví dụ chính sách) |
| BL-06 | Lệch payout giữa sao kê và sàn quá 15% thì chuyển người |
| BL-07 | Tờ trình không thêm số ngoài bằng chứng. Sau hai lần không đạt, lần thứ ba chuyển người kèm bản thảo |
| BL-08 | Thuộc tính nhạy cảm (giới tính, tôn giáo, dân tộc, sức khỏe) không tham gia thẩm định |
| BL-09 | Khoản vay cấp hoàn toàn qua phương tiện điện tử cho cá nhân không vượt 100 triệu đồng dư nợ (Thông tư 06/2023/TT-NHNN). Rule cứng R09. Pháp chế xác nhận cách áp dụng khi đứng tên hộ kinh doanh hoặc mục đích kinh doanh |
| BL-10 | Quyết định phê duyệt có hiệu lực 14 ngày. Quá hạn mà chưa ký hợp đồng thì hồ sơ phải thẩm định lại dữ liệu sàn và CIC |
| BL-11 | Trích thiếu do trần 25% không tính quá hạn trong tối đa 2 kỳ liên tiếp. Kỳ thứ 3 vẫn thiếu thì chuyển Monitor đề xuất cơ cấu. Đây là đề xuất của team, ngân hàng chốt |

Ví dụ case không cần vay: lô 200 triệu, có 120 triệu, cọc 50%, trả nốt 100 triệu cuối tuần 4, payout lịch sử 70 triệu/tuần. Tuần 0 chi 100 triệu, còn 20 triệu. Số dư cuối các tuần 1 đến 4: 90, 160, 230, 200. Không tuần nào âm. Gap = 0. Ngưỡng cảnh báo do engine tính: payout dưới 20 triệu/tuần thì tuần 4 bắt đầu âm. Mọi số ở đây và ở case B đều tái tạo được bằng công thức mục 7, không gõ tay.

Ví dụ case có gap: cùng lô, trả 100% trước, seller xin 150 triệu. Số dư tuần 0 là âm 80, tuần 1 âm 10, tuần 2 dương 60. Gap 80 triệu. Đề xuất 80 × 1,1 = 88, làm tròn lên 90 triệu, dưới trần 92. Kỳ hạn cơ sở 2 tuần âm + 2 = 4 tuần, nhưng 90 ÷ 4 = 22,5 triệu vượt 25% payout xấu (70 × 0,7 × 25% = 12,25), nên kéo lên 6 rồi 8 tuần. Chốt 90 triệu, 8 tuần, 11,25 triệu/tuần, nếu rule cho phép.

## 10. Case nghiệp vụ trong phạm vi

| Mã | Tình huống | Kết quả nghiệp vụ |
|---|---|---|
| A | Điều khoản xưởng khiến không tuần nào âm | Không đề xuất vay, có ngưỡng cảnh báo |
| B | Có gap, xin nhiều hơn gap, nằm trong mức dữ liệu, rule pass | Đề xuất theo gap, người duyệt |
| C | Xin vượt mức, sao kê lệch payout sàn | Chuyển người, không kết luận gian lận |
| D | Xin vượt mức, dòng tiền từ sàn trong sao kê khớp payout, phần vượt là doanh thu ngoài sàn | Vẫn chuyển người xác minh. Dữ liệu sàn không chứng minh được dòng ngoài sàn. Phần payout chỉ bảo đảm mức tương ứng doanh thu sàn. Chưa có trong code: sao kê khớp tổng tiền sàn thì hồ sơ sang `PENDING_REVIEW` |
| E | Fail rule cứng (R01, R02, R04, R05, R06, R08, R09) | Chuyển người. Rule mềm R03, R07 chỉ cảnh báo |
| F | Nội dung nhập vi phạm chặn đầu vào | Không đưa vào thẩm định, không lưu hồ sơ |

## 11. Giả định và câu hỏi mở

Giả định đang dùng:

- Seller đã được ngân hàng định danh và đã consent cho phép đọc dữ liệu sàn phục vụ thẩm định.
- Một hồ sơ tương ứng một lô hàng, một lần đề xuất.
- Lãi, phí, hợp đồng, giải ngân do ngân hàng quyết định và vận hành. Bản giải thích cho seller chỉ được ghi lãi và phí khi ngân hàng đã cung cấp biểu phí.
- “Người thẩm định” là nhân sự ngân hàng. Bấm trên màn hình hồ sơ của Riverbank là ghi nhận quyết định để bàn giao, không thay chữ ký pháp lý trên LOS ngân hàng.

Cần chủ vốn chốt trước khi coi các ngưỡng là chính sách thật: đứng tên cá nhân hay hộ kinh doanh (kéo theo BL-09), hạn mức theo lô hay hạn mức quay vòng, công thức lãi, và xác nhận BL-11 về trích thiếu. Hai câu cuối thuộc vận hành thu nợ của ngân hàng, không chặn phần lõi thẩm định.

## 12. Phê duyệt tài liệu

| Vai trò | Tên | Kết quả | Ngày |
|---|---|---|---|
| Nghiệp vụ / presenter | | | |
| Dev phụ trách phần lõi | | | |
