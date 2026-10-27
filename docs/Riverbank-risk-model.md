# Risk model: dữ liệu huấn luyện, nhãn, kết quả

Bổ sung cho TDD FLOW-04. Trả lời câu hỏi giám khảo chắc chắn sẽ hỏi: PD lấy nhãn ở đâu và tin được đến đâu.

## Sự thật

Không có nhãn vỡ nợ thật. Không nguồn nào cung cấp lịch sử trả nợ của seller Shopee cho một đội hackathon. Vì vậy model huấn luyện trên dữ liệu sinh, hiệu chỉnh theo 60 shop thật đã kéo về ngày 22/10 (`data/synth/calibration.json`, nguồn `v_shop_features`). PD là số minh họa cơ chế, không phải xác suất đã kiểm chứng trên khoản vay thật. Slide và memo đều ghi rõ.

## Hai bộ dữ liệu, không trộn

| Bộ | Bảng | Nguồn | Dùng để |
|---|---|---|---|
| Thật | `shop_snapshot`, `product_snapshot`, `v_shop_features` trên RDS | S3 `raw/addlivetag/<ngày>/` | Hồ sơ demo có tên shop thật, quy mô thật; hiệu chỉnh generator |
| Sinh | `data/synth/train/` (20.000, seed 7), `data/synth/test/` (5.000, seed 11, kèm `oracle.csv`), S3 `synthetic/<ngày>/` | `data/synth/train.py`, `data/synth/oracle.py` | Huấn luyện và đánh giá |

Không ghi cột sinh vào bảng thật. Mỗi cột trong bảng thật phải truy ngược được về file raw.

## Cách sinh (`data/synth/train.py`)

1. Lấy ngẫu nhiên một shop SME thật từ calibration để mượn hình dáng: ngành, AOV, số sản phẩm, độ tập trung top 3, rating. Quy mô payout tuần sinh theo lognormal trung vị 20 triệu, kẹp 5 đến 80 triệu, đúng phân khúc cho vay, vì shop thật kéo về thiên về shop lớn.
2. Sinh 12 tuần payout lịch sử với hệ số biến động theo ngành, tỷ lệ hoàn theo ngành, tuổi shop 2 đến 60 tháng.
3. Chạy `CashFlowEngine` thật với tiền mặt và lịch nhập hàng ngẫu nhiên. Chỉ giữ hồ sơ có gap và số tiền đề xuất không quá 300 triệu (R01).
4. Xác suất sốc là sigmoid của biến động payout, tỷ lệ hoàn, độ tập trung, tuổi shop (dạng `12/(tuổi+12)`) và phần kỳ trích vượt 15% payout xấu. Sốc là payout sụt 50 đến 80% trong 3 đến 8 tuần.
5. Nhãn `label_default = 1` khi có 3 kỳ liên tiếp trích thiếu (payout × 25% nhỏ hơn kỳ trích), đúng BL-11 trong BRD.

Độ mạnh tín hiệu là một lựa chọn thiết kế. Hệ số trong `_risk_score` đã tăng một lần ngày 22/10 vì bản đầu cho trần AUC chỉ 0,60, thấp hơn PD model SME thực tế. Bản hiện tại: default 19%, tăng đơn điệu theo biến động payout (11% ở tercile thấp, 26% ở tercile cao). Calibration chỉ có 2 ngành (electronics, fashion) vì đó là ngành của 41 shop SME thật.

## Trần của dữ liệu (`data/synth/oracle.py`)

Vì biết công thức sinh, ta tính được PD thật của từng hồ sơ test bằng cách chạy lại kỳ trả nợ 200 lần. AUC của PD thật là trần tuyệt đối, mọi số cao hơn là rò rỉ. Trên test 5.000: AUC 0,81, Brier 0,121, sàn Brier 0,122. Model không thể chạm trần vì chỉ thấy cv ước lượng từ 12 tuần, còn oracle biết cv thật.

## Feature (`api/services/features.py`, một nguồn cho train lẫn serving)

Đầu vào thô 14 trường: `archetype`, `shop_age_months`, `n_products`, `top3_share`, `rating`, `return_rate_12w`, `payout_mean_12w`, `payout_cv_12w`, `payout_trend`, `cash_on_hand`, `supplier_amount`, `amount`, `tenor_weeks`, `deduction_to_stress`.
`FeatureBuilder` tạo 25 cột: 11 gốc, 10 dẫn xuất (`age_decay`, `dts_excess`, `log_amount`, `log_payout`, `loan_to_monthly_payout`, `deduction_to_payout`, `cash_weeks_of_deduction`, `cash_cover_ratio`, `tenor_x_cv`, `payout_trend_down`) và one hot ngành.
`shock_weeks`, `short_paid_periods` là hậu quả của kỳ trả nợ, bị loại. Notebook có cell chứng minh để lại thì AUC bằng 1.
`RiskModel.load()` từ chối chạy nếu feature list trong `pd_meta.json` khác `FeatureBuilder.columns`.

## Huấn luyện và đánh giá, chạy ngày 22/10

`eval/train_pd.ipynb` là notebook đầy đủ (EDA, PSI, rò rỉ, trần, baseline, random search, learning curve, ổn định qua seed, calibration, SHAP, ngưỡng nghiệp vụ, model card). `eval/train_pd.py` chạy cùng quy trình không cần notebook. Code dùng chung ở `eval/pdlib.py`.

Train 20.000 (seed 7), test 5.000 (seed 11, không chạm khi chọn tham số). CI 95% bằng bootstrap 300 lần trên test.

| Model | AUC test (CI) | Gini | KS | Brier | ECE | cal slope |
|---|---|---|---|---|---|---|
| Oracle (PD thật) | 0,810 (0,795 đến 0,826) | 0,62 | 0,48 | 0,121 | 0,011 | 0,95 |
| Logistic scorecard, 25 feature | 0,774 (0,756 đến 0,790) | 0,55 | 0,42 | 0,128 | 0,013 | 1,07 |
| LightGBM (4 lá, sâu 4, min 200 mẫu, 435 cây) | 0,772 (0,756 đến 0,789) | 0,54 | 0,42 | 0,128 | 0,022 | 1,15 |

Đọc số:

1. Logistic và LightGBM hòa nhau trong CI. Nhãn gần tuyến tính theo logit nên cây không có lợi thế. Logistic học xong từ 2.000 hồ sơ, LightGBM cần 20.000 mới bắt kịp (learning curve trong notebook).
2. Giữ LightGBM làm model phục vụ vì contract `factors` TreeSHAP đã có trong API. Nói rõ với giám khảo rằng scorecard logistic cho kết quả tương đương và dễ kiểm toán hơn.
3. Calibration: không bật calibrator vì Platt và isotonic không giảm Brier trên OOF. LightGBM hơi thiếu tự tin ở nhóm rủi ro nhất (bin cuối dự đoán 0,52, thực 0,58). Với data thật, fit lại Platt trên validation theo thời gian.
4. Ổn định qua 3 seed test: AUC 0,768 đến 0,783, Brier 0,126 đến 0,133.
5. Notebook ghi ngưỡng PD 0,20 chặn 70% vỡ nợ với approval rate 63%, bad rate nhóm duyệt 9%. Model không tự chọn ngưỡng. Policy Engine dùng rule cứng R10 với `max_pd_bps` 3.000 (PD 30%) trong `policy/parameters.yaml`: vượt trần thì hồ sơ `ESCALATED`, người duyệt xử lý, không tự từ chối. Lý do chọn mốc ở `policy/policy.md` mục R10.
6. SHAP toàn cục xếp hạng đúng cơ chế sinh: biến động payout, tỷ lệ hoàn, tuổi shop, độ tập trung, mức trích so với payout.

Artifact trong image Docker: `api/models/pd_lgbm.txt`, `pd_meta.json` (feature list, hash data, metric, tham số, git sha), `pd_calibrator.json` (enabled=false, có sẵn isotonic và Platt). Backend gọi `services.risk.RiskModel.load().predict(raw)` với 14 trường thô, trả `pd`, `pd_raw`, `factors` top 5 kèm hướng, `model_version`. Seller demo qua model `pd-lgbm-v2` với cùng kế hoạch vay của case B: `minhchau.shop` PD 6,12% (612 bps), yếu tố giảm rủi ro chính là biến động payout thấp và tỷ lệ hoàn 5%. `ngocvy.boutique` cùng payout nhưng shop 7 tháng, hoàn 13%, top 3 chiếm 80%, PD 37,42% (3.742 bps), vượt trần R10.

Khi chấm shop thật, feature thật lấy từ `v_shop_features`, chuỗi payout thật chưa có nên dùng chuỗi mô phỏng co giãn theo `gmv_30d`, memo ghi rõ "payout mô phỏng". Khi có nhãn thật: thay CSV, chạy lại notebook, bỏ phần oracle, thay split ngẫu nhiên bằng split theo thời gian.

## Giám sát khi chạy thật

PSI từng feature so với train mỗi tuần (0,1 cảnh báo, 0,25 train lại), phân phối PD theo tuần, bad rate thực theo cohort khi có nhãn, calibration slope theo quý.

## Lệnh

```
make train-data           # 20.000 hồ sơ seed 7 vào data/synth/train
make test-data            # 5.000 hồ sơ seed 11 vào data/synth/test
make oracle               # PD thật cho tập test, data/synth/test/oracle.csv
make train-pd             # train headless, ghi api/models/
make notebook             # chạy lại eval/train_pd.ipynb từ đầu, có output
aws s3 sync data/synth/train s3://riverbank-data-424016506356/synthetic/$(date +%F)/ --profile riverbank
aws s3 sync data/synth/test s3://riverbank-data-424016506356/synthetic/$(date +%F)/test/ --profile riverbank
```
