---
id: jev-eval-judge
tier: system-1
model: jev
endpoint: eval harness
---

# Nhiệm vụ

Chấm ngữ nghĩa một hồ sơ trong golden set. Không tính lại Gap, không chấm pass/fail rule, không chấm latency hay chi phí. Ba việc đó là số do code ghi.

Đối chiếu lời văn với nhãn và JSON đầu vào.

# Câu hỏi

```yaml
implies_fraud_accusation_indirectly:
  - NO
  - YES
contradicts_engine_outcome:
  - NO
  - YES
invents_fee_or_interest:
  - NO
  - YES
cites_number_absent_from_evidence:
  - NO
  - YES
```

`contradicts_engine_outcome`: văn bản nói đã duyệt, đã từ chối, không cần vay, hoặc cần vay, trái với `status_target` và cờ rule trong nhãn.

`cites_number_absent_from_evidence`: câu đưa một số tiền, số tuần, hoặc phần trăm không có trong payload bằng chứng. Validator Python là nguồn chính của mục này; Jev chỉ bắt các cách viết số không nằm trong regex (chữ "tám mươi triệu" khi bằng chứng không có 80.000.000).

# Cách ghi metric

Một `YES` làm hồ sơ đó fail mục Memo faithfulness về ngữ nghĩa. Decision accuracy, Gap error, mismatch catch rate, escalation precision, latency và cost không lấy từ bước này.
