---
name: eval-golden
description: Thêm golden case, chạy eval offline, đo metric cho engine và memo. Dùng khi đổi engine, thêm case A đến E, chuẩn bị số liệu trình giám khảo, hoặc khi cần biết demo còn đúng không.
---

# Eval và golden set

## Tầng 1: nhãn nghiệp vụ, chạy trong CI

`eval/golden/fx_*.json` với `input` và `expected`. `api/tests/test_golden.py` chấm bằng `eval/score.py`, không cần mạng.
Số lấy từ `policy/worked-examples.md`. Ngưỡng lấy từ `policy/parameters.yaml`. Hai file này thắng slide và TDD khi lệch số.
Thêm case: chép một file FX, đổi `case_id`, điền `expected` đã tính từ file tham số. Chạy `make test`.
Bộ khóa: FX-A, FX-B, FX-C, FX-D, FX-E, FX-F, FX-L.

## Tầng 2: chữ trên tờ trình

`eval/golden/llm/*.json`. CI chạy validator Python trong `eval/score.py`: số khớp bằng chứng, từ cấm, lời văn trái trạng thái.
Jev chấm bốn câu trong `knowledge/prompts/jev-eval-judge.md` khi có harness, không chấm lại Gap.
`eval/run.py` (viết khi có agents):
- Cache LLM theo `sha256(model + prompt)` trong `eval/.cache/`, commit cache để rerun free.
- Metric: faithfulness (Jev score ≥ 4 và citation guard pass), số đúng 100%, latency p50, cost per case.
- Judge khác model sinh. Người kiểm 10 case, ghi vào `eval/REVIEW.md`.

## Báo cáo

`eval/report.md` sinh từ `run.py`: bảng case, pass/fail, cost. Đây là slide số liệu của buổi chấm.

## Không làm

Không sửa `expected` cho khớp slide (90 triệu, 11,25 triệu, 45 triệu) hoặc cho khớp một bản engine đang làm tròn khác `parameters.yaml`. Công thức đổi thì sửa file tham số, worked example, và golden trong cùng một PR.
