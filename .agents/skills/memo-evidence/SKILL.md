---
name: memo-evidence
description: Viết Memo Writer, citation guard và evidence store cho tờ trình tín dụng. Dùng khi đụng FR-21 đến FR-27, FR-38, memo cho người duyệt, giải thích cho seller, hoặc audit trail.
---

# Memo và evidence

Nguyên tắc: mỗi câu trong memo trỏ tới một `evidence_id` có thật trong evidence store của case. Không có evidence thì câu đó bị cắt, không được giữ lại.

## Evidence store

Bảng `evidence(case_id, evidence_id, kind, payload_json, created_at)` append-only trong Postgres.
Mã cố định: E1 đơn hàng, E2 payout, E3 hoàn hàng, E4 feature, E5 bảng dòng tiền và tenor_trace, E6 PD và SHAP, E6a CIC, E7 kết quả rule, E8 doc verifier.
Không có endpoint update hay delete. Sửa là tạo evidence mới.

## Memo Writer

Code: `api/services/underwriting/memo.py`. `plan_slots` chọn câu và evidence, `LlmMemoWriter` chỉ viết lại `text` của từng slot, `claims` và `evidence_id` luôn do code điền. `FallbackMemoWriter` cho LLM lần 1 và 2, lần cuối luôn là template. `MemoValidator` kiểm cả hai. Client chung `api/llm.py`, prompt `knowledge/prompts/memo-writer.md`.

Model: `LLM_MODEL` (mặc định gpt-4.1-mini) qua `LLM_PROVIDER` openai hoặc openrouter. Model dòng gpt-5 không nhận temperature, khi đó bỏ dòng temperature trong front matter của prompt. Prompt dùng template slot, không freeform:
- Đầu vào: số từ engine (`CashFlowResult`, `PolicyOutcome.results`, PD, factors) kèm `evidence_id`.
- Đầu ra: JSON `{"sentences": [{"slot_id", "text"}]}` qua structured output strict. Thiếu hoặc thừa slot là lỗi giao thức, rơi về template ngay lần đó.
- Mọi số trong `text` phải xuất hiện trong payload evidence tương ứng. Citation guard so chuỗi số, lệch thì loại câu và log drift.
- Tối đa 2 lần sửa. Lần 3 vẫn lỗi thì Escalated kèm memo thô.

## Giải thích cho seller (FR-28)

Ngắn, tiếng Việt, 3 câu: gap bao nhiêu, đề xuất bao nhiêu và vì sao, kỳ trích mỗi tuần. Với gap 0: ngưỡng cảnh báo payout. Không nêu PD, không nêu nhóm nợ CIC.

## Chấm chéo

Eval dùng Claude (model khác model sinh) và Jev score 1 đến 5 cho faithfulness. Người kiểm 10 case. Xem skill `eval-golden`.

## Không làm

Không để LLM thấy số thô ngoài payload evidence. Không cho LLM tính lại gap hay kỳ trích. Không in evidence payload có PII vào log.
