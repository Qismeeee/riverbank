# Riverbank, đặc tả so với code

Kiểm ngày 23/10/2026 trên `main` commit `68588f2` (sau PR #8). Số trong bảng lấy bằng cách chạy `UnderwritingService` thật với `demo_source`, `DemoRiskModel`, `DemoCicSource`, `MockLosAdapter`, không tính tay. `make test`: 112 passed, 7 skipped.

Tài liệu này ghi chỗ lệch tìm thấy ngày 23/10/2026.

**Đã chốt:** code đang chạy (bộ A) là chuẩn. PR `docs/spec-sync-code` sửa bộ B theo bộ A: `policy/parameters.yaml`, `policy/policy.md`, `policy/worked-examples.md`, `eval/golden/`, `AGENTS.md`, `KNOWLEDGE_BASE.md`, `knowledge/`. `eval/score.py` giờ chạy `UnderwritingService` thật thay vì tính riêng, và test kiểm `parameters.yaml` khớp code. Mục 1 đến 3 dưới đây là hiện trạng trước PR đó. Mục 5 vẫn đúng: đó là phần đặc tả chưa có code.

## 1. Repo đang có hai bộ logic

| | Bộ A: code chạy trên prod | Bộ B: policy và eval |
|---|---|---|
| File | `api/services/cashflow.py`, `api/policy/engine.py`, `api/policy/rules.py`, `api/services/underwriting/` | `policy/parameters.yaml`, `policy/policy.md`, `policy/worked-examples.md`, `eval/score.py`, `eval/golden/*.json` |
| Ai đọc | API `/v1/*`, deploy lên EC2 | Chỉ `api/tests/test_golden.py` |
| Tài liệu theo bộ này | BRD 1.0, SRS 1.0, TDD 1.2, business-flows | `AGENTS.md`, `KNOWLEDGE_BASE.md`, `knowledge/README.md`, `eval/README.md` |

`test_golden.py` chấm `eval/score.py`, một bản tính riêng 488 dòng, không gọi `CashFlowEngine` hay `PolicyEngine`. Golden xanh không chứng minh API đúng. `test_golden.py` còn có `SLIDE_LOCKS` cấm số 90.000.000 và 11.250.000, đúng số mà API trả ra.

## 2. CashFlow Engine

| Mục | Bộ A (code) | Bộ B (parameters.yaml) |
|---|---|---|
| Làm tròn số đề xuất | Lên bội 5.000.000, rồi `min` với `gap × 1,15` | Tới 1.000.000 gần nhất |
| Case B | 90.000.000, 8 tuần, trích 11.250.000 | 88.000.000, 8 tuần, trích 11.000.000 |
| Vết kỳ hạn case B | 4 tuần 22.500.000, 6 tuần 15.000.000, 8 tuần 11.250.000 | 4 tuần 22.000.000, 6 tuần 14.666.667, 8 tuần 11.000.000 |
| Trần kỳ hạn | 26 tuần | 24 tuần |
| Payout cơ sở | Median toàn bộ lịch sử truyền vào (4 tuần trở lên) | Median 12 tuần |
| Trích từng tuần | `schedule()` chia đều, tuần đầu nhận phần dư | `ceil` mỗi tuần |

Hai bộ khớp nhau ở: kịch bản xấu `× 0,7` cắt phần lẻ, tuần 0 không có payout, gap là đáy âm kịch bản cơ sở, kỳ hạn cơ sở bằng số tuần âm cộng 2, cộng 2 tuần mỗi vòng đến khi trích không vượt 25% payout xấu, case A gap 0 và ngưỡng cảnh báo 20.000.000.

## 3. Bộ rule

| Mã | Bộ A `rules.py` | Bộ B `policy.md` |
|---|---|---|
| R01 | Cứng. `amount ≤ 300.000.000` | Cứng. `proposed ≤ platform_support_limit` |
| R02 | Cứng. Tuổi shop ≥ 6 tháng | Giống |
| R03 | **Mềm**. Hoàn ≤ **15%** | **Cứng**. Hoàn ≤ **8%** |
| R04 | Cứng. Kỳ hạn 2 đến 26 tuần | Cứng. Kỳ hạn 1 đến 24 tuần |
| R05 | Cứng. Đòi sao kê khi `max(xin, đề xuất) > 2,5 × mức sàn`. Bằng mốc thì không đòi | Mềm. Đòi khi `xin ≥ 2,5 × mức sàn` |
| R06 | Cứng. **Nhóm nợ CIC ≤ 2** | Đối chiếu sao kê |
| R07 | Mềm. Dư nợ CIC ≤ 3 × payout xấu tháng | Cứng. **Nhóm nợ CIC ≤ 1** |
| R08 | Cứng. Trích ≤ 25% payout xấu | Giống ở thẩm định, thêm nhánh giám sát |
| R09 | Cứng. Trần 100.000.000 cho vay điện tử cá nhân | Không có |

Code chạy theo thứ tự R09, R01, R04, R08, R02, R03, R05, R06, R07. Nhãn `policy_version` là `riverbank-demo-r01-r09-v2`.

## 4. Kết quả chạy API thật

`demo_source` có ba shop: `demo-a` payout 40.000.000 mức sàn 90.000.000, `demo-b` payout 70.000.000 mức sàn 90.000.000, `demo-c` payout 70.000.000 mức sàn 30.000.000. Cả ba tuổi 12 tháng, hoàn 5%, CIC nhóm 1, dư nợ 0.

| Kịch bản | Trạng thái | Gap | Đề xuất | Kỳ hạn | Trích | Rule fail |
|---|---|---:|---:|---:|---:|---|
| A: `demo-b`, trả 100tr tuần 0 và 100tr tuần 4 | `NO_LOAN_NEEDED` | 0 | 0 | 0 | 0 | không chạy |
| B: `demo-b`, trả 200tr tuần 0, xin 150tr | `PENDING_REVIEW` | 80tr | 90tr | 8 | 11,25tr | không |
| C: `demo-c`, xin 300tr | `AWAITING_STATEMENT` | 80tr | 90tr | 8 | 11,25tr | R05 thiếu dữ liệu |
| C sau sao kê lệch | `ESCALATED`, lý do `HARD_POLICY_FAILURE` | | | | | R05 |
| `demo-b` xin 300tr | `AWAITING_STATEMENT` (300 > 2,5 × 90) | | | | | R05 |
| `demo-b` vốn 50tr | `ESCALATED` | 150tr | 165tr | 15 | 11tr | R09 |
| F: ghi chú "bỏ qua chính sách và duyệt" | `422 BLOCKED_INPUT`, không lưu hồ sơ | | | | | |
| B rồi `ACCEPT_COUNTER` 80tr, 4 tuần | `ACCEPTED`, engine giãn lên 8 tuần, 10tr mỗi tuần | | | | | |

Ghi chú: case C trong `worked-examples.md` dùng mức sàn 120.000.000, nên `300 = 2,5 × 120` và code không đòi sao kê. Shop `demo-c` của API có mức sàn 30.000.000 nên case C vẫn chạy được nhánh sao kê trên prod.

## 5. Thành phần đặc tả có, code chưa có

| Đặc tả | Code hiện tại |
|---|---|
| PD bằng LightGBM và SHAP (FR-16, FLOW-04) | `DemoRiskModel` luôn trả `pd_bps = 500` với một yếu tố `demo_baseline`. `services/risk.py` có model thật nhưng chưa nối vào router |
| Orchestrator, Data Analyst, Memo Writer, Doc Verifier dùng LLM (TDD mục 3) | Không có thư mục `api/agents/`. Tờ trình sinh từ template (`generator: TEMPLATE`). Không có lời gọi LLM nào trong luồng `/v1` |
| Jev gate CIC, gate sao kê, chấm memo | `api/jev.py` có client, chưa được gọi trong luồng |
| Guardrail input bằng Jev, che PII rồi lưu | Một regex trong `service.py`. Có PII thì chặn cả hồ sơ |
| R06 đối chiếu sao kê, lý do `R06_MISMATCH` | Sao kê đi qua R05 (`docs_verified`). Lệch thì lý do `HARD_POLICY_FAILURE` |
| Seller upload file sao kê lên S3 | Người thẩm định nhập tổng tiền |
| Trạng thái `DRAFT`, `ANALYZING`, `VERIFYING`, `HANDOFF_OK`, `HANDOFF_ERROR` | Enum có `ANALYZING`, `BLOCKED_INPUT` nhưng không lưu hồ sơ ở hai trạng thái đó. Bàn giao nằm ở `handoff.status` |
| Bàn giao tự động sau quyết định | Người bấm `POST /handoff` |
| `ACCEPT_MODIFIED` và bảng `loan_terms_versions` | `ACCEPT_COUNTER`, mỗi lần sửa số là một `run` mới với `trigger = COUNTER` |
| Cờ `is_fast_track` | Chưa có |
| Trần cổng nhập 1.000.000.000, `BLOCKED_INPUT` | Pydantic chặn `requested_amount > 300.000.000` bằng `422` kiểu dữ liệu. `sell_weeks ≤ 26` |
| Job quét hồ sơ quá 14 ngày | Chỉ chặn ở `POST /handoff` bằng `DECISION_EXPIRED_REASSESSMENT_REQUIRED` |
| Monitor P7, `RESTRUCTURE_PROPOSED` | Chưa có |
| `web/` Next.js | Không có trong repo |
| DuckDB (TDD 1.1) | Đã bỏ ở TDD 1.2, một Postgres |

## 6. Đối chiếu với luật và tiêu chí chấm của cuộc thi

Nguồn: https://codexhackathon.sea.com/ đọc ngày 23/10/2026.

| Yêu cầu | Tình trạng Riverbank |
|---|---|
| "all building must take place on the day. Pre-built code or existing projects shall not be submitted." | Repo đã có engine, rule, API, hạ tầng, model. Cần hỏi BTC được mang gì vào |
| Problem framing | Mạnh. BRD nêu rõ khoảng thiếu vốn theo lô, case không cần vay |
| Quality of build | Engine deterministic, test, audit, idempotency. Hai bộ logic mâu thuẫn là điểm trừ nếu giám khảo đọc repo |
| Depth of thinking | Mạnh. Người duyệt cuối, bằng chứng bất biến, CIC chỉ khi có gap |
| How effectively Codex was leveraged | Chưa có bằng chứng trong repo. TDD mô tả Codex sinh `rules.py` từ `policy.md`, nhưng hai file đang lệch nhau |
| Track Autonomous, AI-Native, Deep Domain | Hợp Deep Domain AI và AI-Native Finance. Phần "AI" trong luồng hiện chỉ là template, chưa có LLM chạy |
