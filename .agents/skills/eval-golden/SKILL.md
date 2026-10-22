---
name: eval-golden
description: Thêm golden case, chạy eval offline, đo metric cho engine và memo. Dùng khi đổi engine, thêm case A đến E, chuẩn bị số liệu trình giám khảo, hoặc khi cần biết demo còn đúng không.
---

# Eval và golden set

## Tầng 1: engine, chạy trong CI

`eval/golden/*.json` với `input` và `expected`. `api/tests/test_golden.py` đọc tự động, không cần mạng.
Thêm case: chép file có sẵn, đổi `case_id`, điền `expected` đã tính tay theo TDD FLOW-03. Chạy `make test`.
Case A đến E trong `docs/Riverbank-BRD.md` mục 9 phải có đủ ở đây trước ngày demo.

## Tầng 2: LLM, chạy tay

`eval/golden/llm/*.json` cho memo, doc verifier, orchestrator. `eval/run.py` (viết khi có agents):
- Cache LLM theo `sha256(model + prompt)` trong `eval/.cache/`, commit cache để rerun free.
- Metric: faithfulness (Jev score ≥ 4 và citation guard pass), số đúng 100%, latency p50, cost per case.
- Judge khác model sinh. Người kiểm 10 case, ghi vào `eval/REVIEW.md`.

## Data huấn luyện risk model

`data/synth/train.py` sinh `train_shops.csv` (feature lúc thẩm định + `label_default`) và `train_weeks.csv`, hiệu chỉnh từ `data/synth/calibration.json` (60 shop thật). Nhãn mô phỏng theo BL-11, cách sinh và feature ghi ở `docs/Riverbank-risk-model.md`. Đổi quy tắc sinh thì cập nhật doc đó và `api/tests/test_train_data.py`.

## Báo cáo

`eval/report.md` sinh từ `run.py`: bảng case, pass/fail, cost. Đây là slide số liệu của buổi chấm.

## Không làm

Không sửa `expected` cho khớp code. Code sai thì sửa code. Nếu docs sai thì sửa docs, code và golden cùng PR.
