---
name: jev-decisions
description: Dùng Jev (typesafe/jev-1.14 qua OpenRouter Decisions API, client api/jev.py) cho câu hỏi có/không, phân loại, chấm mức, kiểm ảnh. Dùng khi cần gate trước bước tốn tiền (CIC, vision), chấm chéo memo, hoặc phân loại giấy tờ.
---

# Jev decisions

Client: `api/jev.py`. Key: `OPENROUTER_API_KEY` qua `config.py` (local `.env`, prod secret `riverbank/openrouter`).
Chạy thử: `make jev`.

## Jev là gì, dùng ở đâu

Jev trả xác suất cho câu hỏi có kiểu, không sinh chữ. Rẻ (0,042 USD / 1M token input), nhanh, không cần parse JSON.
Dùng cho:
- Gate CIC: "hồ sơ này đủ điều kiện tra CIC?" trước khi gọi API ngân hàng tốn phí. Ngưỡng 0,6.
- Gate Document Verifier: "ảnh này có phải sao kê ngân hàng?" trước khi gọi vision.
- Chấm chéo memo: score 1 đến 5 cho "mỗi câu có bám evidence?" trong eval.
- Phân loại lý do từ chối, loại giấy tờ, mức độ khớp doanh thu ngoài sàn.

Không dùng cho: tính số (engine làm), viết memo (model text làm), quyết định cuối (người làm).

## Cách gọi

```python
from jev import choice, decide, noul, score

state = ["gap 80 triệu, shop 14 tháng, hoàn hàng 5%, payout xấu 49 triệu/tuần"]
answers = decide(state, {
    "eligible_for_cic": noul("Hồ sơ đủ điều kiện tra CIC?", "đủ", "không"),
    "risk": score("Mức rủi ro dòng tiền?", ["Thấp", "Vừa", "Cao"]),
    "doc_type": choice("Loại giấy tờ?", {"statement": "sao kê", "invoice": "hóa đơn", "other": "khác"}),
})
p_yes = answers["eligible_for_cic"]["probabilities"]["true"]   # xem key thật bằng `make jev`
eligible = p_yes >= 0.6
```
`state` là list (chuỗi, hoặc `image_part(path)` cho ảnh). `questions` là dict tên → câu hỏi. `decide` trả dict cùng tên, mỗi phần tử là xác suất theo từng đáp án. Chạy `make jev` một lần để thấy đúng tên key rồi viết helper `prob(answers, name, label)` trong `jev.py`, mọi chỗ khác gọi helper đó. Code đặt ngưỡng và ghi ngưỡng vào evidence.

## Quy tắc

- `state` chỉ chứa số và nhãn đã tính, không dán nguyên memo hay PII.
- Mỗi lần gọi lưu `(question, probabilities, threshold, decision)` vào `case_events` với `evidence_id`.
- Lỗi mạng hoặc 401: trả `missing_data`, không mặc định pass. Hard rule sẽ đẩy Escalated.
- Timeout 30s, không retry quá 2 lần.
- Trong test: mock `jev.decide`, không gọi mạng.
