# Risk model: dữ liệu huấn luyện và nhãn

Bổ sung cho TDD FLOW-04. Trả lời câu hỏi giám khảo chắc chắn sẽ hỏi: PD lấy nhãn ở đâu.

## Sự thật

Không có nhãn vỡ nợ thật. Không nguồn nào cung cấp lịch sử trả nợ của seller Shopee cho một đội hackathon. Vì vậy model huấn luyện trên dữ liệu sinh, hiệu chỉnh theo 60 shop thật đã kéo về ngày 22/10 (`data/synth/calibration.json`, nguồn `v_shop_features`). PD là số minh họa cơ chế, không phải xác suất đã kiểm chứng trên khoản vay thật. Slide và memo đều ghi rõ.

## Hai bộ dữ liệu, không trộn

| Bộ | Bảng | Nguồn | Dùng để |
|---|---|---|---|
| Thật | `shop_snapshot`, `product_snapshot`, `v_shop_features` trên RDS | S3 `raw/addlivetag/<ngày>/` | Hồ sơ demo có tên shop thật, quy mô thật; hiệu chỉnh generator |
| Sinh | `data/synth/train/train_shops.csv`, `train_weeks.csv` (S3 `synthetic/<ngày>/`) | `data/synth/train.py`, seed cố định | Huấn luyện và đánh giá LightGBM |

Không ghi cột sinh vào bảng thật. Mỗi cột trong bảng thật phải truy ngược được về file raw.

## Cách sinh (`data/synth/train.py`)

1. Lấy ngẫu nhiên một shop SME thật từ calibration để mượn hình dáng: ngành, AOV, số sản phẩm, độ tập trung top 3, rating. Quy mô payout tuần sinh theo lognormal trung vị 20 triệu, kẹp 5 đến 80 triệu, đúng phân khúc cho vay, vì shop thật kéo về thiên về shop lớn.
2. Sinh 12 tuần payout lịch sử với hệ số biến động theo ngành, tỷ lệ hoàn theo ngành, tuổi shop 2 đến 60 tháng.
3. Chạy `CashFlowEngine` thật với tiền mặt và lịch nhập hàng ngẫu nhiên. Chỉ giữ hồ sơ có gap và số tiền đề xuất không quá 300 triệu (R01).
4. Xác suất sốc tăng theo biến động payout, tỷ lệ hoàn, độ tập trung, shop trẻ, kỳ trích sát trần 25%. Sốc là payout sụt 35 đến 75% trong 2 đến 6 tuần.
5. Nhãn `label_default = 1` khi có 3 kỳ liên tiếp trích thiếu (payout × 25% nhỏ hơn kỳ trích), đúng BL-11 trong BRD.

Kết quả với 3.000 hồ sơ, seed 7: tỷ lệ default 19,4%, tăng đơn điệu theo biến động payout (15% → 23%), tỷ lệ hoàn, độ tập trung, và giảm theo tuổi shop. Tín hiệu có nhưng không quá mạnh, đủ để SHAP cho ra giải thích hợp lý.

## Feature cho model (tại thời điểm thẩm định, không nhìn tương lai)

`archetype`, `shop_age_months`, `n_products`, `top3_share`, `rating`, `return_rate_12w`, `payout_mean_12w`, `payout_cv_12w`, `payout_trend`, `amount`, `tenor_weeks`, `deduction_to_stress`.
Không đưa `shock_weeks`, `short_paid_periods` vào feature, chúng là hậu quả, chỉ để đối chiếu.

## Huấn luyện

LightGBM binary, split theo `shop_id`, báo AUC và calibration trên tập test. SHAP top 5 theo từng hồ sơ là `factors` trong `risk_results`. Model lưu `models/pd_lgbm.txt` kèm `models/pd_meta.json` (seed, n, ngày, AUC). Khi chấm shop thật, feature thật lấy từ `v_shop_features`, chuỗi payout thật chưa có nên dùng chuỗi mô phỏng co giãn theo `gmv_30d`, memo ghi rõ "payout mô phỏng".

## Lệnh

```
make train-data           # sinh data/synth/train/*.csv và in tóm tắt
aws s3 sync data/synth/train s3://riverbank-data-424016506356/synthetic/$(date +%F)/ --profile riverbank
```
