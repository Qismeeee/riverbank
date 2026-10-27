---
name: memo-evidence
description: Viết tờ trình (Memo Writer bằng code), citation guard và evidence store cho tờ trình tín dụng. Dùng khi đụng FR-21 đến FR-27, FR-38, memo cho người duyệt, giải thích cho seller, hoặc audit trail.
---

# Memo và evidence

Nguyên tắc: mỗi câu trong memo trỏ tới một `evidence_id` có thật trong evidence store của case. Không có evidence thì câu đó bị cắt, không được giữ lại.

## Evidence store

Bảng `evidence(case_id, evidence_id, kind, payload_json, created_at)` append-only trong Postgres.
Mã cố định: E1 đơn hàng, E2 payout, E3 hoàn hàng, E4 feature, E5 bảng dòng tiền và tenor_trace, E6 PD và SHAP, E6a CIC, E7 kết quả rule, E8 doc verifier.
Không có endpoint update hay delete. Sửa là tạo evidence mới.

## Memo Writer

Code: `api/services/underwriting/memo.py`. `plan_slots` chọn câu và evidence. `CodeMemoWriter` viết mọi câu bằng mẫu trong `TEMPLATES` (`evidence.py`), không gọi LLM. Mỗi câu mang `claims`, `evidence_id` và `semantic_frame` (actor, relation, roles, units, audience) lấy từ `FRAME_SPECS`. Câu có số luôn nói rõ chủ thể: "Hệ thống đề xuất", "Mô hình ước tính", "Policy Engine kết luận".

`MemoValidator` kiểm từng câu: claims khớp evidence, số có trong evidence và đủ, đúng thứ tự, không từ cấm, không nói ngược trạng thái, frame và chữ đúng nguyên văn mẫu (`MEMO_SEMANTIC_MISMATCH`), đủ slot đúng thứ tự (`MEMO_SLOT_MISMATCH`). Lúc duyệt validator chạy lại. Tờ trình cũ chưa có `semantic_frame` thì chỉ kiểm theo luật số, để hồ sơ tạo trước khi đổi vẫn duyệt được.

Đổi câu: sửa `TEMPLATES` và `FRAME_SPECS` cùng lúc, chạy `make test` và `make memo-gate`. Muốn LLM viết lại câu thì phải có cách kiểm chủ thể và quan hệ, kiểm theo số là không đủ.

## Giải thích cho seller (FR-28)

Ngắn, tiếng Việt, 3 câu: gap bao nhiêu, đề xuất bao nhiêu và vì sao, kỳ trích mỗi tuần. Với gap 0: ngưỡng cảnh báo payout. Không nêu PD, không nêu nhóm nợ CIC.

## Chấm chéo

Tờ trình: `make memo-gate` và test tiêm lỗi trong `test_golden.py`. Copilot: `make llm-eval`, có `--judge` cho Jev và `review.csv` để người chấm. Xem skill `eval-golden`.

## Không làm

Không để LLM thấy số thô ngoài payload evidence. Không cho LLM tính lại gap hay kỳ trích. Không in evidence payload có PII vào log.
