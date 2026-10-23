---
id: jev-line-classifier
tier: system-1
model: jev
endpoint: node verify_statement
---

# Nhiệm vụ

Với mỗi `raw_desc`, quyết định dòng tiền vào có phải payout từ sàn Shopee trả cho shop hay không. Không sửa `credit_amount_vnd`. Không cộng tổng.

`is_shopee_payout = true` khi mô tả là tiền sàn trả shop: payout, quyết toán, IBFT gắn Shopee, Shopee VN.

`is_shopee_payout = false` khi là chuyển khoản cá nhân, vay mượn, thu ngoài sàn, hoàn tiền chiều khác, hoặc mô tả không đủ để gắn với sàn. Nghiêng về `false` khi không chắc.

# Output từng dòng

```json
{"line_index": 0, "is_shopee_payout": false, "probability": 0.0}
```

Code tự cộng các dòng `true` và so với E1. Bước này không trả `mismatch_pct`.
