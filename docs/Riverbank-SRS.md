# SRS — Riverbank

| | |
|---|---|
| Sản phẩm | Riverbank |
| Loại tài liệu | Software Requirements Specification |
| Phiên bản | 1.1 |
| Trạng thái | Bản làm việc cho team |
| Bám theo | `Riverbank-BRD.md` phiên bản 1.0 |
| Không nằm ở đây | Lựa chọn model, thư viện, số agent. Xem `riverbank-architecture.md` |

**Thay đổi 1.1 so với 1.0:** thêm mục 11 ghi mức đáp ứng của code ngày 23/10/2026. Số và rule lấy theo code đang chạy (R01 đến R09 trong `api/policy/rules.py`, đề xuất case B 90 triệu). Bảng mã trạng thái mục 7 ghi rõ trạng thái nào code lưu.

**Thay đổi 1.0 so với 0.9:** sửa ví dụ nghiệm thu FR-12, FR-13, FR-14 theo công thức; thêm FR-16b (CIC), FR-19b (trần cho vay điện tử), FR-31b (hiệu lực quyết định); FR-32 chạy lại rule khi duyệt số khác; bảng mã trạng thái chuẩn ở mục 7.

Mỗi yêu cầu chức năng dùng chữ **phải**. Một yêu cầu là đạt khi điều kiện nghiệm thu của nó đúng, không phụ thuộc tên công nghệ bên trong.

---

## 1. Mục đích

SRS mô tả phần mềm Riverbank phải làm gì trong phạm vi lõi: tiếp nhận một kế hoạch lô hàng, thẩm định khoảng thiếu vốn, lập hồ sơ có bằng chứng, ghi nhận quyết định của người thẩm định, và bàn giao gói hồ sơ cho LOS ngân hàng.

## 2. Phạm vi hệ thống

Hệ thống gồm các khả năng: chặn đầu vào, lấy dữ liệu sàn, tính dòng tiền và gap, chấm rủi ro, chạy chính sách, đối chiếu sao kê khi cần, viết tờ trình, hàng đợi cho người thẩm định, ghi quyết định, bàn giao.

Hệ thống không gồm eKYC, CIF, kết nối CIC trực tiếp, soạn hợp đồng, khế ước, ủy nhiệm chi, hạch toán giải ngân, trích nợ, phân nhóm nợ. Các hệ thống đó là hệ thống ngoài. Yêu cầu đối với chúng chỉ ở mức giao diện bàn giao, mục FR-HO.

## 3. Thuật ngữ

| Từ | Nghĩa trong SRS |
|---|---|
| Gap | Số dư tiền mặt âm sâu nhất trên dòng tiền tuần, kịch bản cơ sở |
| Kịch bản cơ sở | Payout mỗi tuần bằng mức lịch sử đã chọn |
| Kịch bản xấu | Payout mỗi tuần bằng kịch bản cơ sở nhân hệ số xấu |
| Bằng chứng | Một bảng hoặc kết quả tính có mã, người đọc tờ trình mở lại được |
| Mức dữ liệu sàn hỗ trợ | Trần số tiền mà chính sách cho phép đề xuất chỉ dựa trên dữ liệu sàn |
| Rule cứng | Rule mà kết quả fail buộc chuyển người |
| Gói hồ sơ | Tờ trình, bản seller, kết quả rule, kết quả rủi ro, bảng dòng tiền, quyết định đã ghi |

## 4. Hệ thống ngoài

| Hệ thống | Riverbank cần | Riverbank không làm |
|---|---|---|
| Dữ liệu sàn | Đơn, payout theo tuần, hoàn hàng, giao trễ, rating, tuổi shop của seller đã consent | Sửa dữ liệu sàn |
| Chính sách | Bộ rule đang hiệu lực, do chủ vốn duyệt | Tự đặt rule thay ngân hàng |
| Biểu phí | Lãi và phí để ghi vào bản seller, nếu ngân hàng đã cung cấp | Tự định giá |
| CIC, qua ngân hàng | Kết quả tra cứu theo yêu cầu của hồ sơ có gap, gắn mã bằng chứng | Mở kết nối CIC trực tiếp; tra khi chưa có gap |
| LOS ngân hàng | Nhận gói hồ sơ và trạng thái | Soạn hợp đồng, khế ước, ủy nhiệm chi |
| Core banking | Không gọi trong phạm vi SRS này | Giải ngân và thu nợ |
| Người thẩm định | Danh tính người bấm quyết định | Ủy quyền pháp lý. Việc đó thuộc ngân hàng |

## 5. Yêu cầu chức năng

### 5.1 Tiếp nhận

**FR-01.** Hệ thống phải tạo một hồ sơ cho mỗi lần seller gửi kế hoạch, với định danh hồ sơ duy nhất.
Nghiệm thu: hai lần gửi tạo hai hồ sơ.
Bám BR-01.

**FR-02.** Hồ sơ phải lưu các trường: định danh seller, giá trị lô, vốn đang có, tỷ lệ hoặc số tiền phải trả xưởng ở từng mốc tuần, số tuần bán hết, số tiền xin vay.
Nghiệm thu: thiếu một trường bắt buộc thì hồ sơ không vào thẩm định và người nhập nhận được lỗi chỉ ra trường đó.
Bám BR-01.

**FR-03.** Trước khi bất kỳ bước thẩm định nào đọc nội dung tự do, hệ thống phải chặn hoặc làm sạch: số điện thoại, số định danh, số tài khoản; lệnh yêu cầu bỏ qua chính sách hoặc ép duyệt; thuộc tính nhạy cảm.
Nghiệm thu: một ghi chú chứa lệnh ép duyệt không làm đổi kết quả rule. Nội dung bị chặn không xuất hiện trong tờ trình.
Bám BR-02, BL-08.

**FR-04.** Hệ thống phải từ chối số tiền xin vay hoặc số tuần vượt trần cứng đã cấu hình, và không chạy thẩm định.
Nghiệm thu: giá trị vượt trần trả lỗi, trạng thái hồ sơ không sang thẩm định.
Bám BR-02.

### 5.2 Dữ liệu sàn

**FR-05.** Hệ thống phải lấy dữ liệu của đúng seller trên hồ sơ, trong đó có payout theo tuần của các tuần lịch sử đã cấu hình.
Nghiệm thu: hồ sơ seller A không chứa payout của seller B.
Bám BR-03.

**FR-06.** Mỗi bảng dữ liệu dùng cho kết luận phải có mã bằng chứng.
Nghiệm thu: tờ trình trích payout trung bình thì mở được bảng nguồn của mã đó.
Bám BR-17.

**FR-07.** Hệ thống phải từ chối thẩm định khi seller chưa có cờ consent hiệu lực.
Nghiệm thu: hồ sơ không consent dừng với lý do consent, không phát sinh tờ trình.
Bám BR-03.

### 5.3 Dòng tiền và gap

Phần tính phải cho cùng một đầu vào thì cùng một kết quả. Người vận hành không sửa số dư bằng cách sửa câu chữ.

**FR-08.** Hệ thống phải dựng lịch chi theo điều khoản xưởng và lịch thu theo payout lịch sử, theo từng tuần, cho kịch bản cơ sở và kịch bản xấu.
Nghiệm thu: case A trong BRD cho gap = 0. Case trả 100% trước cho gap đúng số dư âm sâu nhất, sai lệch trong ngưỡng kiểm thử đã chốt.
Bám BR-04, BR-05, BL-01.

**FR-09.** Số dư cuối một tuần phải bằng số dư đầu tuần cộng payout tuần đó trừ khoản chi tuần đó. Số dư đầu tuần sau bằng số dư cuối tuần trước.
Nghiệm thu: cộng tay bảng xuất ra khớp công thức trên từng tuần.
Bám BR-05.

**FR-10.** Gap phải bằng giá trị tuyệt đối của số dư âm sâu nhất trên kịch bản cơ sở. Không có số dư âm thì gap phải bằng 0.
Nghiệm thu: đổi tuần âm sâu nhất thì gap đổi theo đúng tuần đó, không theo tổng các tuần âm.
Bám BR-06.

**FR-11.** Khi gap = 0, hệ thống phải dừng trước chấm rủi ro và chạy rule vay, ghi trạng thái không đề xuất vay, và xuất lý do cùng ngưỡng payout cảnh báo.
Nghiệm thu: case A không có điểm rủi ro và không có đề xuất số tiền vay.
Bám BR-06, BR-07.

**FR-12.** Khi gap lớn hơn 0, số tiền đề xuất phải bằng gap nhân hệ số đệm, và không vượt gap nhân trần đệm. Số tiền này không được gán bằng số seller xin nếu số xin khác kết quả trên.
Nghiệm thu: xin 150, gap 80, hệ số 1,1, trần 1,15, làm tròn lên bội 5 triệu thì đề xuất 90 (88 làm tròn lên, dưới trần 92), không ra 150.
Bám BR-08, BL-02.

**FR-13.** Kỳ hạn đề xuất phải bằng số tuần có số dư âm liên tục tính từ tuần bắt đầu âm đến tuần hết âm, cộng số tuần đệm.
Nghiệm thu: case B âm tuần 0 và tuần 1, đệm 2, thì kỳ hạn cơ sở là 4 tuần, trước khi FR-14 kéo dài.
Bám BR-09, BL-03.

**FR-14.** Nếu khoản trừ một tuần lớn hơn tỷ lệ trần của payout kịch bản xấu, hệ thống phải tăng kỳ hạn theo bước đã cấu hình và tính lại khoản trừ, cho đến khi không còn vượt hoặc chạm trần kỳ hạn.
Nghiệm thu: case B, 90 triệu chia 4 tuần là 22,5 triệu, vượt 25% payout xấu (12,25 triệu), kéo lên 6 rồi 8 tuần; khoản trừ cuối 11,25 triệu không vượt 12,25.
Bám BR-10, BL-04.

**FR-15.** Đầu ra tính dòng tiền phải gồm gap, tuần bắt đầu âm, tuần kết thúc âm, số tiền, kỳ hạn, khoản trừ từng tuần, và hai bảng số dư. Đầu ra này phải có mã bằng chứng.
Bám BR-05, BR-17.

### 5.4 Rủi ro và chính sách

**FR-16.** Khi gap lớn hơn 0, hệ thống phải ghi một xác suất không trả được và danh sách yếu tố giải thích, mỗi yếu tố gắn chiều làm tăng hoặc giảm xác suất.
Nghiệm thu: hồ sơ có gap luôn có xác suất trong khoảng 0 đến 1 và có ít nhất một yếu tố.
Bám BR-11.

**FR-16b.** Khi gap lớn hơn 0, hệ thống phải yêu cầu kết quả CIC qua giao diện của ngân hàng trước khi chạy rule, gắn kết quả vào hồ sơ với mã bằng chứng. Khi gap bằng 0 hệ thống không được yêu cầu CIC. Không nhận được kết quả thì rule phụ thuộc CIC ghi thiếu dữ liệu và hồ sơ chuyển người.
Nghiệm thu: case A không phát sinh yêu cầu CIC. Case B có một bằng chứng CIC trước bản ghi rule. Bản demo dùng nguồn CIC mô phỏng.
Bám BR-21.

**FR-17.** Hệ thống phải chạy từng rule của bộ chính sách đang hiệu lực và lưu pass hoặc fail, kèm mã rule.
Nghiệm thu: tắt một rule trong bộ đã duyệt thì hồ sơ chạy lại không còn kết quả của rule đó.
Bám BR-12.

**FR-18.** Bộ rule đưa vào chạy phải là bộ đã được người duyệt. Hệ thống phải từ chối nạp bộ rule chưa qua kiểm thử của chính bộ đó.
Bám BR-12.

**FR-19.** Nếu một rule đánh dấu là rule cứng bị fail, trạng thái hồ sơ phải là chuyển người. Không được tạo đề xuất duyệt thẳng.
Nghiệm thu: fail rule cứng, bấm duyệt thẳng bị từ chối bởi hệ thống.
Bám BR-13, BL-05.

**FR-19b.** Bộ rule phải có rule cứng về trần dư nợ cho vay qua phương tiện điện tử theo BL-09, giá trị trần đặt trong cấu hình, mặc định 100 triệu đồng.
Nghiệm thu: xin 300 triệu đứng tên cá nhân thì rule này fail và hồ sơ chuyển người, kể cả khi dữ liệu sàn hỗ trợ.
Bám BL-09.

**FR-20.** Văn bản giải thích không được đổi pass thành fail hoặc ngược lại so với kết quả FR-17.
Nghiệm thu: sửa câu trong tờ trình không làm đổi bản ghi kết quả rule.
Bám BR-12.

### 5.5 Sao kê

**FR-21.** Hệ thống chỉ được yêu cầu sao kê khi số tiền xin vượt mức dữ liệu sàn hỗ trợ.
Nghiệm thu: case B, đề xuất nằm trong mức hỗ trợ, không có bước nộp sao kê.
Bám BR-14.

**FR-22.** Khi đã có sao kê, hệ thống phải đọc các dòng tiền vào được ghi là từ sàn và so với payout sàn cùng kỳ.
Nghiệm thu: lệch trên ngưỡng thì trạng thái là chuyển người và có bảng đối chiếu.
Bám BR-15, BL-06.

**FR-23.** Bảng đối chiếu và tờ trình không được chứa kết luận gian lận. Từ ngữ cho phép là không khớp và cần xác minh.
Nghiệm thu: rà soát văn bản case lệch, không có từ gian lận.
Bám BR-15.

### 5.6 Tờ trình

**FR-24.** Hệ thống phải tạo hai văn bản từ cùng bộ kết quả: tờ trình cho người thẩm định, và bản giải thích cho seller.
Bám BR-16.

**FR-25.** Mỗi câu trong tờ trình có chứa số phải gắn một mã bằng chứng. Số trong câu phải khớp số của bằng chứng đó.
Nghiệm thu: bỏ mã của một câu có số thì văn bản không được chuyển cho người thẩm định. Câu có số lệch bằng chứng cũng không được chuyển.
Bám BR-17, BL-07.

**FR-26.** Nếu tờ trình không đạt FR-25, hệ thống phải yêu cầu viết lại tối đa hai lần. Lần thứ ba không đạt thì chuyển người kèm bản chưa đạt, và ghi rõ bản chưa đạt.
Bám BL-07.

**FR-27.** Bản cho seller phải gồm: có đề xuất vay hay không; nếu có thì số tiền, kỳ hạn, khoản trừ từng kỳ; lãi, phí và tổng phải trả chỉ khi đã có biểu phí từ ngân hàng. Không có biểu phí thì bản phải ghi là chưa có số lãi và phí, không được điền số tự tính.
Bám BR-16.

**FR-28.** Khi không đề xuất vay, bản cho seller phải nói rõ lý do theo dòng tiền và ngưỡng payout cảnh báo.
Bám BR-07.

### 5.7 Quyết định của người thẩm định

**FR-29.** Hệ thống phải đưa hồ sơ đã đạt các cổng trên vào hàng đợi, với trạng thái một trong các giá trị: không đề xuất vay, chờ duyệt, chuyển người.
Bám BR-18.

**FR-30.** Người dùng có quyền thẩm định phải xem được tờ trình và mở được từng bằng chứng.
Bám BR-18.

**FR-31.** Với hồ sơ chờ duyệt, người có quyền phải ghi được một trong các quyết định: chấp nhận đề xuất, chấp nhận với số tiền hoặc kỳ hạn khác, từ chối.
Nghiệm thu: quyết định lưu tên người bấm và thời điểm.
Bám BR-18.

**FR-31b.** Quyết định chấp nhận có hiệu lực trong số ngày cấu hình, mặc định 14. Quá hạn mà chưa có bước tiếp theo thì hồ sơ phải chạy lại từ FR-05 với dữ liệu mới.
Bám BL-10.

**FR-32.** Khi người chọn số tiền hoặc kỳ hạn khác đề xuất, hệ thống phải tính lại khoản trừ theo FR-14, chạy lại toàn bộ rule FR-17 trên bộ số mới, và chỉ lưu quyết định khi không fail rule cứng. Fail thì quyết định bị từ chối với mã rule.
Nghiệm thu: người duyệt nâng số tiền vượt trần R01 thì hệ thống không ghi nhận quyết định.
Bám BR-18, BR-10, BR-12.

**FR-33.** Hồ sơ chuyển người không được nhận quyết định chấp nhận đề xuất gốc nếu chưa có ghi nhận xác minh của người thẩm định.
Bám BR-13, BR-15.

**FR-34.** Hồ sơ không đề xuất vay không được chuyển sang quyết định cho vay nếu người dùng không tạo một hồ sơ mới và chạy lại từ FR-02.
Bám BR-06.

### 5.8 Bàn giao cho LOS ngân hàng

Đây là ranh giới với phần ngân hàng kế thừa. Không có yêu cầu sinh hợp đồng, khế ước hay ủy nhiệm chi.

**FR-35.** Khi hồ sơ đã có quyết định chấp nhận hoặc từ chối, hệ thống phải đóng gói: định danh hồ sơ, định danh seller, quyết định, số tiền, kỳ hạn, lịch trừ, tờ trình, mã bằng chứng, kết quả rule, kết quả rủi ro, định danh người quyết định, thời điểm.
Nghiệm thu: gói của hồ sơ từ chối không chứa số tiền giải ngân. Gói của hồ sơ chấp nhận chứa đúng số đã ghi ở FR-31 hoặc FR-32.
Bám BR-19.

**FR-36.** Hệ thống phải xuất gói FR-35 qua một giao diện mà LOS ngân hàng đọc được, và lưu mã tham chiếu do LOS trả về nếu LOS trả mã.
Nghiệm thu: gọi giao diện thất bại thì hồ sơ giữ quyết định đã ghi và trạng thái bàn giao lỗi, không làm mất quyết định.
Bám BR-19.

**FR-37.** Hệ thống không được gửi lệnh giải ngân, lệnh hạch toán, hoặc lệnh trích nợ sang core.
Nghiệm thu: rà soát giao diện trong phạm vi bản này, không có lệnh tạo khoản vay trên core.
Bám phạm vi mục 2.

### 5.9 Nhật ký

**FR-38.** Mỗi lần đổi trạng thái, hệ thống phải ghi: trạng thái trước, trạng thái sau, tác nhân, thời điểm, danh sách mã bằng chứng đã dùng. Tác nhân là người hoặc một bước hệ thống có tên.
Nghiệm thu: mở một hồ sơ case B thấy đủ các lần đổi từ lúc tạo đến lúc bàn giao.
Bám BR-20.

## 6. Dữ liệu tối thiểu của hồ sơ

| Nhóm | Trường |
|---|---|
| Hồ sơ | Mã hồ sơ, mã seller, trạng thái, thời điểm tạo |
| Kế hoạch | Giá trị lô, vốn đang có, lịch chi xưởng, số tuần bán, số xin vay |
| Dòng tiền | Gap, tuần đầu âm, tuần hết âm, số đề xuất, kỳ hạn, khoản trừ, hai bảng tuần |
| Rủi ro | Xác suất, danh sách yếu tố |
| Chính sách | Mã bộ rule, kết quả từng rule |
| CIC | Mã bằng chứng CIC, nhóm nợ, thời điểm tra |
| Sao kê | Có yêu cầu hay không, kết quả lệch, bảng đối chiếu |
| Văn bản | Tờ trình, bản seller, mã bằng chứng trên từng câu có số |
| Quyết định | Loại quyết định, số tiền chốt, kỳ hạn chốt, người, thời điểm |
| Bàn giao | Trạng thái gửi LOS, mã tham chiếu LOS |

Bằng chứng lưu nội dung hoặc tham chiếu bất biến tới bảng đã dùng lúc thẩm định. Sửa dữ liệu sàn sau đó không được làm đổi bằng chứng của hồ sơ đã đóng.

## 7. Trạng thái hồ sơ trong phạm vi

```text
Nháp
  -> Bị chặn đầu vào
  -> Đang thẩm định
        -> Không đề xuất vay
        -> Chờ bổ sung sao kê -> Đang đối chiếu -> Chờ duyệt hoặc Chuyển người
        -> Chờ duyệt
        -> Chuyển người
Chờ duyệt hoặc Chuyển người
  -> Đã chấp nhận
  -> Đã từ chối
Đã chấp nhận hoặc Đã từ chối
  -> Đã bàn giao hoặc Bàn giao lỗi
```

Không có đường từ Đang thẩm định tới Đã chấp nhận.

Mã trạng thái dùng trong code và tên hiển thị, dùng thống nhất ở TDD và tài liệu luồng nghiệp vụ:

| Mã trong code | Tên tiếng Việt | Tên trong tài liệu luồng |
|---|---|---|
| DRAFT | Nháp | Draft |
| BLOCKED_INPUT | Bị chặn đầu vào | Rejected_Input |
| ANALYZING | Đang thẩm định | Analyzing |
| NO_LOAN_NEEDED | Không đề xuất vay | No_Loan_Needed |
| AWAITING_STATEMENT | Chờ bổ sung sao kê | Docs_Required |
| VERIFYING | Đang đối chiếu | Verifying |
| PENDING_REVIEW | Chờ duyệt | Proposed |
| ESCALATED | Chuyển người | Escalated |
| ACCEPTED | Đã chấp nhận | Approved |
| REJECTED | Đã từ chối | Declined |
| HANDOFF_OK, HANDOFF_ERROR | Đã bàn giao, Bàn giao lỗi | Offer_Sent trở đi (mô hình A) |

Code ngày 23/10/2026 lưu hồ sơ ở `NO_LOAN_NEEDED`, `AWAITING_STATEMENT`, `PENDING_REVIEW`, `ESCALATED`, `ACCEPTED`, `REJECTED`. `DRAFT` và `ANALYZING` chỉ có trong nhật ký vì thẩm định chạy xong trong một yêu cầu. `BLOCKED_INPUT` trả `422` và không lưu hồ sơ. `VERIFYING` chưa có, nhập sao kê chạy lại thẩm định ngay. Bàn giao ghi ở `handoff.status` (`SUCCEEDED`, `FAILED`) chứ không đổi trạng thái hồ sơ.

Các trạng thái sau ACCEPTED (Offer_Sent, Contract_Signed, Disbursed) thuộc mô hình A trong tài liệu luồng nghiệp vụ, ngoài phạm vi SRS lõi, bản demo mô phỏng.

## 8. Yêu cầu phi chức năng

| Mã | Yêu cầu | Mức với bộ demo |
|---|---|---|
| NFR-01 | Một hồ sơ từ lúc gửi hợp lệ đến lúc vào hàng đợi hoặc trạng thái không đề xuất vay phải xong trong giới hạn thời gian đã chốt | Dưới 90 giây trên bộ demo |
| NFR-02 | Cùng đầu vào và cùng bộ rule, kết quả gap, số tiền, kỳ hạn và kết quả rule phải giống nhau giữa hai lần chạy | Khớp tuyệt đối trên phần tính |
| NFR-03 | Tỷ lệ câu có số trong tờ trình đã phát hành có mã bằng chứng hợp lệ | 100% trên bộ demo |
| NFR-04 | Thuộc tính nhạy cảm không có trong dữ liệu đưa vào chấm rủi ro và không có trong tờ trình | Rà soát bộ demo |
| NFR-05 | Nhật ký trạng thái không bị sửa hoặc xóa bởi người dùng thường | Không có chức năng sửa nhật ký |
| NFR-06 | Chi phí xử lý một hồ sơ demo nằm trong trần team đã chốt | Dưới 0,10 USD trên bộ demo nếu có phát sinh chi phí model |

NFR-01 và NFR-06 là mục tiêu bản demo trong tài liệu kiến trúc, chưa phải cam kết vận hành thật.

## 9. Thứ tự làm

| Hạng | Yêu cầu |
|---|---|
| Trước | FR-01 đến FR-20 (gồm FR-16b, FR-19b), FR-24 đến FR-34 (gồm FR-31b), FR-38 |
| Cùng lúc nếu kịp | FR-21 đến FR-23 |
| Có đầu ra, không đào sâu | FR-35 đến FR-37 |
| Không làm trong bản này | Hợp đồng, khế ước, ủy nhiệm chi, giải ngân, trích nợ, re-score sau giải ngân |

## 10. Truy vết

| BRD | SRS |
|---|---|
| BR-01 | FR-01, FR-02 |
| BR-02 | FR-03, FR-04 |
| BR-03 | FR-05, FR-07 |
| BR-04, BR-05 | FR-08, FR-09, FR-15 |
| BR-06, BR-07 | FR-10, FR-11, FR-28, FR-34 |
| BR-08, BR-09, BR-10 | FR-12, FR-13, FR-14, FR-32 |
| BR-11 | FR-16 |
| BR-12, BR-13 | FR-17, FR-18, FR-19, FR-20 |
| BR-14, BR-15 | FR-21, FR-22, FR-23, FR-33 |
| BR-16, BR-17 | FR-06, FR-24, FR-25, FR-26, FR-27 |
| BR-18 | FR-29, FR-30, FR-31 |
| BR-19 | FR-35, FR-36, FR-37 |
| BR-20 | FR-38 |
| BR-21 | FR-16b |

## 11. Mức đáp ứng của code

Kiểm ngày 23/10/2026 trên `main` commit `68588f2`, chạy `UnderwritingService` thật. Chi tiết và số ở `docs/Riverbank-spec-vs-code.md`.

| FR | Mức | Ghi chú |
|---|---|---|
| FR-01, FR-02 | Đạt | `POST /v1/cases`, Pydantic báo đúng trường thiếu |
| FR-03 | Đạt | Regex chặn cả hồ sơ khi có lệnh ép duyệt, SĐT, CCCD, số tài khoản, thuộc tính nhạy cảm. FR-03 cho phép chặn hoặc làm sạch, code chọn chặn. Ngoài regex này không bước nào đọc `note` |
| FR-04 | Đạt | Trần `requested_amount` 300.000.000, `sell_weeks` 26, trả `422` |
| FR-05, FR-07 | Đạt | Snapshot đúng `seller_id` và `shop_id`, thiếu consent thì `422 CONSENT_REQUIRED` |
| FR-06 | Đạt | Mã E4, E5, E6, E6a, E7, E8, băm lại mỗi lần đọc |
| FR-08 đến FR-15 | Đạt | Case B: gap 80.000.000, đề xuất 90.000.000, 8 tuần, 11.250.000. Case A: gap 0, cảnh báo 20.000.000 |
| FR-16 | Đạt | LightGBM `pd-lgbm-v2`, tối đa 5 yếu tố SHAP. Thiếu hồ sơ shop thì `ESCALATED`, `RISK_DATA_MISSING` |
| FR-16b | Đạt với nguồn mock | Jev gate ngưỡng 0,6 trước CIC. `DemoCicSource` trả nhóm 1, dư nợ 0. Không gọi khi gap 0. Gate từ chối hoặc lỗi thì R06 thiếu dữ liệu, chuyển người. Người duyệt tra CIC thủ công được qua `POST /cic-inquiry` |
| FR-17 | Đạt | Mười rule R01 đến R10, mỗi rule có `evidence_id` |
| FR-18 | Đạt một phần | Không có bước nạp bộ rule đã ký. Có `policy_hash`, đổi rule sau thẩm định thì chặn `ACCEPT_PROPOSAL` |
| FR-19, FR-19b | Đạt | Rule cứng fail thì `ESCALATED`. R09 trần 100.000.000 |
| FR-20 | Đạt | Prod viết tờ trình bằng LLM (`RIVERBANK_MEMO_WRITER=llm`) trên số đã khóa, lỗi thì quay về template. Không ghi ngược vào kết quả rule |
| FR-21 | Đạt | Đòi sao kê khi `max(xin, đề xuất) > 2,5 ×` mức sàn. Bằng đúng mốc thì không đòi |
| FR-22 | Đạt một phần | Người thẩm định nhập tổng tiền sàn trên sao kê, chưa đọc file. Lệch trên 15% thì `ESCALATED` |
| FR-23 | Đạt | Thông điệp "không khớp; cần xác minh" |
| FR-24, FR-25 | Đạt | Hai bản từ cùng bằng chứng, mỗi câu có `evidence_id`, số khớp payload |
| FR-26 | Đạt | Thử tối đa 3 lần, LLM nhận lỗi validator của lần trước. Template chỉ là đường lui |
| FR-27, FR-28 | Đạt | Bản seller không có lãi, phí, PD, CIC. Không vay thì nêu ngưỡng cảnh báo |
| FR-29, FR-30 | Đạt | `GET /v1/queue`, `GET /v1/cases/{id}`, `GET /v1/cases/{id}/evidence/{evidence_id}` |
| FR-31, FR-32 | Đạt | `ACCEPT_PROPOSAL`, `ACCEPT_COUNTER`, `REJECT`. Counter giãn kỳ hạn, chấm lại PD và chạy lại mười rule |
| FR-31b | Đạt một phần | `expires_at` 14 ngày, chặn ở bàn giao. Chưa có job quét đưa hồ sơ về thẩm định lại |
| FR-33, FR-34 | Đạt | Thiếu `verification_note` hoặc gap 0 thì `409` |
| FR-35, FR-36 | Đạt | Gói bàn giao tạo khi quyết định, người bấm `POST /handoff`, `MockLosAdapter`. Lỗi LOS không mất quyết định |
| FR-37 | Đạt | Không có lệnh sang core |
| FR-38 | Đạt | `case_events` chỉ thêm, có tác nhân và mã bằng chứng |
| NFR-01 | Đạt, chưa đo trên prod | Thẩm định chạy xong trong yêu cầu. Engine và rule dưới 1 giây. Trên prod còn một lần gọi Jev (timeout 10 giây) và LLM viết tờ trình, chưa đo p50 |
| NFR-02 | Đạt | Engine và rule thuần, golden khóa số |
| NFR-06 | Chưa đo | Prod gọi Jev qua OpenRouter, Copilot và Memo Writer gọi `gpt-4.1-mini`. Chưa có số chi phí mỗi hồ sơ |
