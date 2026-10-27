---
name: eval-golden
description: Thêm golden case, chạy eval offline, đo metric cho engine và memo. Dùng khi đổi engine, thêm case A đến E, chuẩn bị số liệu trình giám khảo, hoặc khi cần biết demo còn đúng không.
---

# Eval và golden set

## Tầng 1: nhãn nghiệp vụ, chạy trong CI

`eval/golden/fx_*.json` với `input` và `expected`. `api/tests/test_golden.py` chấm bằng `eval/score.py`, không cần mạng.
Số lấy từ `policy/worked-examples.md`. Ngưỡng lấy từ `policy/parameters.yaml`. Hai file này thắng slide và TDD khi lệch số.
Thêm case: chép một file FX, đổi `case_id`, điền `expected` đã tính từ file tham số. Chạy `make eval-report` để sinh lại `api/studio/eval_report.json` (Studio đọc file này), rồi `make test`.
Bộ khóa: FX-A, FX-B, FX-C, FX-D, FX-E, FX-F, FX-L.

## Tầng 2: chữ trên tờ trình

`eval/golden/llm/*.json`. CI chạy validator Python trong `eval/score.py`: số khớp bằng chứng, từ cấm, lời văn trái trạng thái, câu khóa phải đúng nguyên văn (`locked_text`).
Tờ trình do code viết. `make memo-gate` chạy mọi golden case qua service thật, kể cả nộp sao kê, so mọi trường nhãn, exit 1 khi lệch. Test tiêm lỗi trong `test_golden.py` dùng `eval/inject.py`.

## Tầng 3: Copilot với model thật

`make llm-eval` (`eval/llm_eval.py`) gọi model thật, không chạy trong CI. Trích kế hoạch: 12 tin nhắn có nhãn ở `eval/golden/copilot/extract.json`, đếm trường đúng, bỏ trống, sai, bịa. Hỏi đáp: 5 câu hỏi trên mỗi golden case, đếm câu qua guard và mã guard. `--judge` gọi Jev chấm bốn câu trong `knowledge/prompts/jev-eval-judge.md`. Kết quả ở `eval/out/<thời điểm>/`: `report.json` và `review.csv` có cột `human_ok` cho người chấm. Kết quả review tay ghi vào `eval/REVIEW.md`.

## Data huấn luyện risk model

`data/synth/train.py` sinh `train_shops.csv` (feature lúc thẩm định + `label_default`) và `train_weeks.csv`, hiệu chỉnh từ `data/synth/calibration.json` (60 shop thật). Nhãn mô phỏng theo BL-11, cách sinh và feature ghi ở `docs/Riverbank-risk-model.md`. Đổi quy tắc sinh thì cập nhật doc đó, `api/tests/test_train_data.py`, và chạy lại `make oracle` vì trần AUC đổi theo. Feature chỉ thêm ở `api/services/features.py` (FeatureBuilder), train và serving cùng đọc một chỗ, `RiskModel.load()` từ chối meta lệch feature. Train lại: `make train-data && make test-data && make oracle && make train-pd` (headless) hoặc `make notebook` (đầy đủ, có output). Kết quả hiện tại: AUC test 0,77, trần 0,81, logistic và LightGBM hòa nhau. Đừng tuning thêm, mọi số vượt trần là rò rỉ; việc đáng làm là feature từ `train_weeks.csv` hoặc nhãn thật khi có.

## Báo cáo

Số trình giám khảo lấy từ `api/studio/eval_report.json` (`make eval-report`), `make memo-gate` và `report.json` của `make llm-eval` gần nhất.

## Không làm

Không sửa `expected` cho khớp slide (90 triệu, 11,25 triệu, 45 triệu) hoặc cho khớp một bản engine đang làm tròn khác `parameters.yaml`. Công thức đổi thì sửa file tham số, worked example, và golden trong cùng một PR.
