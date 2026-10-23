# TDD — Riverbank

| | |
|---|---|
| Sản phẩm | Riverbank |
| Loại tài liệu | Technical Design Document |
| Phiên bản | 1.3 |
| Trạng thái | Bản làm việc cho team |
| Bám theo | `Riverbank-BRD.md` 1.0, `Riverbank-SRS.md` 1.0 |
| Tham chiếu | `riverbank-architecture.md` |
| Đối tượng đọc | Dev Lead, Dev, QA, presenter kỹ thuật |
| Mục tiêu chi phí | ≤ **0,10 USD / hồ sơ** (NFR-06); latency ≤ **90 giây** (NFR-01) |

**Thay đổi 1.3 so với 1.2:** thêm mục 0 trạng thái triển khai theo code ngày 23/10/2026; FLOW-05 ghi đúng chín rule R01 đến R09 đang chạy; mục 7 ghi đúng API đang chạy, chi tiết ở `Riverbank-API.md`; mục 9 cấu trúc repo theo repo thật; thêm pgweb ở mục 9b.

**Thay đổi 1.2 so với 1.1:** bỏ DuckDB, một DB Postgres; thêm ETL dữ liệu công khai (`api/etl/`, nguồn data.addlivetag.com) với raw trên S3, bảng snapshot và view `v_shop_features`; nguồn dữ liệu xem `Riverbank-data.md`.

**Thay đổi 1.1 so với 1.0:** chốt stack theo repo và hạ tầng đã dựng (Next.js trên Vercel, FastAPI trên EC2, Postgres RDS, DuckDB, S3, Secrets Manager, GitHub Actions); thêm Jev vào model routing; thêm bước CIC sau gap; sửa số ví dụ (90 triệu, 8 tuần, 11,25 triệu/tuần); FLOW-08 chạy lại Policy Engine khi duyệt số khác; thêm mục 9b hạ tầng và CI/CD; cấu trúc repo mục 9 theo repo thật.

Tài liệu này ghi **cách hệ thống được xây**: stack, từng luồng kỹ thuật, hợp đồng API, mô hình dữ liệu, và cách cắt chi phí model mà vẫn giữ kỷ luật thẩm định.

Nguyên tắc cứng (từ kiến trúc): **LLM chỉ điều phối, lập luận và viết. Mọi con số về gap, rủi ro, rule và hạn mức do code deterministic tính.**

## 0. Trạng thái triển khai (23/10/2026)

Kiểm trên `main` commit `68588f2`. Số và rule theo code là chuẩn. Phần ghi "kế hoạch" là thiết kế chưa có code. Chi tiết lệch ở `Riverbank-spec-vs-code.md`.

| Luồng | Trạng thái | Code |
|---|---|---|
| FLOW-01 Tiếp nhận | Có. Regex chặn cả hồ sơ, chưa che PII, chưa có Jev | `routers/underwriting.py`, `services/underwriting/service.py` |
| FLOW-02 Consent, dữ liệu sàn | Có. `demo_source` hoặc `PostgresFeatureSource`. Không có Data Analyst LLM | `services/underwriting/sources.py` |
| FLOW-03, FLOW-03a Dòng tiền | Có, đúng số mục này | `services/cashflow.py` |
| FLOW-04 PD, CIC | PD là `LightGbmRiskModel`, thiếu hồ sơ shop thì `RISK_DATA_MISSING`. CIC là `DemoCicSource`. Chưa có Jev gate | `services/underwriting/risk.py`, `sources.py` |
| FLOW-05 Policy | Có, chín rule | `policy/engine.py`, `policy/rules.py` |
| FLOW-06 Sao kê | Có một phần. Người thẩm định nhập tổng tiền sàn, so ngưỡng 15%. Chưa upload, chưa OCR | `service.submit_statement` |
| FLOW-07 Tờ trình | Có bằng template, kiểm từng câu với bằng chứng. Chưa có Memo Writer LLM | `services/underwriting/evidence.py` |
| FLOW-08 Quyết định | Có. `ACCEPT_PROPOSAL`, `ACCEPT_COUNTER`, `REJECT`. Chưa có job quét hồ sơ quá 14 ngày | `service.decide` |
| FLOW-09 Bàn giao | Có. Người bấm, `MockLosAdapter` | `service.handoff` |
| FLOW-10 Audit | Có. `case_events` chỉ thêm | `service._event` |
| FLOW-11 Eval | Có golden chạy qua service thật. Chưa có metric latency, cost, LLM judge | `eval/score.py`, `api/tests/test_golden.py` |
| FLOW-12 Monitor | Kế hoạch | |
| Mục 6 Orchestrator FSM | Kế hoạch. Luồng hiện là hàm tuần tự `_assess` | |
| Agent LLM (mục 2.2, mục 3) | Kế hoạch. Chưa có `api/agents/`, luồng `/v1` không gọi model nào | |
| `web/` Lender Portal | Chưa có trong repo | |

---

## 1. Mục tiêu kỹ thuật

| Mã | Mục tiêu | Cách đo |
|---|---|---|
| T-01 | Deterministic trên phần tính | Cùng input + cùng rule set → gap, số tiền, kỳ hạn, pass/fail giống nhau (NFR-02) |
| T-02 | Evidence-first | 100% câu có số trong memo gắn `evidence_id` hợp lệ (NFR-03) |
| T-03 | Cost-bounded | Chi phí model trung bình &lt; 0,10 USD/hồ sơ trên golden set |
| T-04 | Human-in-the-loop | Không có đường Analyzing → Approved không qua người |
| T-05 | Fail-closed | Fail rule cứng / lệch sao kê / memo không đạt → chuyển người, không tự duyệt |

---

## 2. Quyết định stack (cost-optimized, 2026)

### 2.1 Tổng quan lựa chọn

| Lớp | Công nghệ | Vì sao | Chi phí vận hành |
|---|---|---|---|
| Runtime agent | OpenAI Agents SDK / Responses API + structured outputs | Hackathon Codex; tool-calling ổn định; schema cứng | Chỉ trả cho token thật sự cần |
| Orchestration | State machine tường minh (Python) + LLM lập kế hoạch ngắn | Giảm vòng lặp agent “tự nghĩ”; cap ≤ 12 bước | Ít call hơn multi-agent chat thuần |
| Tính gap / lịch trừ | Python thuần (NumPy optional) | Deterministic, 0 USD model | Free |
| Analytics store | Postgres (RDS), bảng `shop_snapshot`, `product_snapshot`, view `v_shop_features` | Một DB duy nhất cho cả feature và trạng thái app; Data Analyst chỉ SELECT trên view; raw JSON gốc trên S3 (bản 1.0 ghi DuckDB, bỏ để giảm thành phần) | Trong RDS |
| Feature / payout | View SQL + Feature Service | Cache theo `seller_id` | Free |
| Risk / PD | LightGBM + SHAP (local) | PD + giải thích offline | Free |
| Policy | Policy-as-code (`rules.py` + pytest), sinh bởi Codex từ `policy.md` | Rule không bị LLM sửa | Free |
| Guardrail input | Regex + denylist + Presidio-lite / spaCy patterns | Chặn trước khi tốn token | Free |
| Guardrail output | Validator Python (số ↔ evidence) | Không cần LLM để kiểm số | Free |
| Memo / giải thích seller | Model nhỏ OpenAI (gpt-4.1-mini hoặc gpt-5-mini) + template slot | Viết văn bản, không tính số | ~0,02–0,05 USD/case |
| Text-to-SQL (Data Analyst) | Model nhỏ + whitelist view + dry-run | Chỉ SELECT; schema nhỏ | ~0,01–0,03 USD/case |
| Document Verifier | Vision chỉ khi FR-21 kích hoạt; ưu tiên CSV/JSON demo | Tránh vision mọi case | 0 USD đa số case; ~0,03–0,06 khi có PDF |
| Retrieval policy clause | rank_bm25 + text-embedding-3-small, gộp RRF | Trích đúng clause cho memo; index nằm trong RAM api, nạp từ S3 lúc start | ~0 |
| Quyết định có/không, phân loại | Jev (typesafe/jev-1.14) qua OpenRouter Decisions API | Trả xác suất, không sinh chữ, không parse; dùng cho gate trước bước đắt và chấm chéo | 0,042 USD / 1M token input, output free |
| API | FastAPI | Async, OpenAPI tự sinh | Free |
| UI | Next.js 15 trên Vercel | Form seller, queue, case view, eval tab; gọi API qua HTTPS | Free (hobby) |
| App DB | PostgreSQL 18 trên RDS db.t4g.micro, bảng `cases`, `case_events`, `jobs` | Trạng thái, audit FR-38, job queue bằng `SKIP LOCKED` thay SQS | ~10 USD / 15 ngày |
| Object store | S3 `riverbank-data-424016506356`, versioning | Sao kê, memo xuất, snapshot DuckDB, dump | ~0 |
| Secrets | AWS Secrets Manager, EC2 đọc qua IAM role | OPENAI, ANTHROPIC, OPENROUTER, RDS master | ~1,6 USD / tháng |
| Observability | CloudWatch Logs (awslogs driver) + 3 alarm SNS + cost meter theo case_id trong `case_events` | Đủ cho 1 máy; OpenTelemetry khi có nhiều service | Free |
| Eval | Pytest + golden JSONL + LLM-as-judge có người kiểm mẫu | NFR + pitch | Judge chỉ trên subset |

### 2.2 Model routing (tối ưu tiền)

```text
                    ┌─────────────────────────────┐
  Input sạch ──────►│ Router (deterministic rules) │
                    └──────────────┬──────────────┘
           ┌───────────────────────┼───────────────────────┐
           ▼                       ▼                       ▼
   [0 token]              [cheap model]            [vision / larger]
   Gap, PD, Policy,       Orchestrator plan,       Doc Verifier PDF
   Guardrails, Template   Data Analyst SQL,        (chỉ khi vượt mức)
   fill số                Memo Writer
```

| Bước | Model | Ghi chú tiết kiệm |
|---|---|---|
| Guardrail in/out | Không | Code |
| Cash flow / gap | Không | Code |
| Risk PD + SHAP | Không | LightGBM local |
| Policy Engine | Không | Python rules |
| Orchestrator | Small | Structured output, max 12 steps, prompt cache schema |
| Data Analyst | Small | Schema card ≤ 2KB; cấm `SELECT *` rộng; cache kết quả theo seller+window |
| Memo Writer | Small | Nhận **JSON số đã chốt**, chỉ viết câu; temperature 0 |
| Doc Verifier | Jev (ảnh có phải sao kê, khớp loại giấy tờ) rồi mới vision / small | Jev gate ~0 USD; vision chỉ khi Jev nói đúng loại giấy tờ |
| Gate CIC, gate eligibility | Jev noul | Xác suất đủ điều kiện trước khi tốn phí tra CIC; ngưỡng đặt trong code |
| LLM-as-judge | Claude (model khác model sinh) + Jev score cho faithfulness | Chỉ 10–20% golden set; người kiểm 10 case; judge khác model sinh để tránh tự chấm |

**Budget mục tiêu mỗi hồ sơ (demo):**

| Hạng mục | USD ước lượng |
|---|---|
| Orchestrator (1–3 turn) | 0,015 |
| Data Analyst (1–2 SQL) | 0,020 |
| Memo Writer (2 bản) | 0,030 |
| Vision (case C only) | 0,040 |
| **Case A (gap=0)** | **~0,04** |
| **Case B (đường chính)** | **~0,065** |
| **Case C (có sao kê)** | **~0,10** |

Kỹ thuật cắt thêm:

1. **Prompt caching** schema tool + policy clause cố định.
2. **Early exit** khi gap = 0: bỏ Risk, Policy vay, Doc Verifier.
3. **Cache seller features** TTL trong phiên demo.
4. **Memo template có slot**: LLM chỉ điền giải thích; số do code inject.
5. **Không parallel spam agents**: tuần tự có điều kiện; Document Verifier lazy.
6. **Demo hot path**: pre-warm 3 case A/B/C vào cache kết quả deterministic; LLM vẫn chạy thật trên 1 case “live”.

---

## 3. Kiến trúc runtime

```mermaid
flowchart TB
  subgraph Edge["Edge / UI"]
    UI_S[Seller Form]
    UI_U[Underwriter Queue + Case]
    UI_E[Eval Tab]
  end

  subgraph API["FastAPI"]
    GW[Auth + Consent check]
    ORCH[Orchestrator FSM]
    COST[Cost / Latency meter]
  end

  subgraph Agents["LLM Agents — cheap model"]
    DA[Data Analyst]
    CFA[Cash Flow Agent facade]
    PA[Policy Agent]
    MW[Memo Writer]
    DV[Document Verifier]
  end

  subgraph Det["Deterministic Services — $0"]
    GR_I[Guardrail Input]
    GR_O[Guardrail Output]
    FS[Feature Service]
    CF_E[CashFlow Engine]
    RM[LightGBM + SHAP]
    PE[Policy Engine]
    EV[Evidence Store]
    EH[Eval Harness]
  end

  subgraph Data["Data"]
    APP[(Postgres RDS: feature snapshot, cases, events, jobs)]
    OBJ[(S3: sao kê, memo, snapshot)]
    SM[(Secrets Manager)]
  end

  UI_S --> GW --> GR_I --> ORCH
  ORCH --> DA --> DB
  ORCH --> CFA --> CF_E --> FS --> DB
  ORCH --> RM
  ORCH --> PA --> PE
  ORCH -.-> DV
  DV --> FS
  ORCH --> MW --> GR_O --> UI_U
  ORCH --> APP
  EV --> APP
  COST --> APP
  EH --> ORCH
  EH --> UI_E
  DV --> OBJ
```

**Ranh giới trách nhiệm**

| Thành phần | Được làm | Không được làm |
|---|---|---|
| Orchestrator FSM | Gọi bước, ghi state, enforce cap bước | Tự bịa số tiền / pass-fail |
| Cash Flow Agent | Chọn tham số cấu hình đã whitelist, gọi engine, giải thích | Tính gap bằng LLM |
| Policy Agent | Trích clause, gọi PE | Ghi đè kết quả rule |
| Memo Writer | Viết câu từ JSON kết quả | Thêm số ngoài evidence |
| Risk Model | PD + SHAP | Quyết định duyệt |
| Jev | Trả xác suất cho câu hỏi có kiểu; code đặt ngưỡng | Thay engine tính số; thay người duyệt |

---

## 4. Mô hình dữ liệu tối thiểu

### 4.1 Bảng ứng dụng

| Entity | Trường chính | Ghi chú |
|---|---|---|
| `cases` | `case_id`, `seller_id`, `status`, `created_at`, `consent_ok` | FR-01, FR-07 |
| `case_plans` | lot_value, cash_on_hand, supplier_schedule[], sell_weeks, amount_requested | FR-02 |
| `evidence` | `evidence_id`, `case_id`, `kind`, `payload_hash`, `payload_ref`, `immutable` | FR-06; không mutate sau close |
| `cashflow_results` | gap, first_neg_week, last_neg_week, amount, tenor_weeks, weekly_deduction[], base_table, stress_table, evidence_id | FR-15 |
| `risk_results` | pd, factors[{name, direction, shap}], evidence_id | FR-16 |
| `cic_results` | bureau_group, outstanding, queried_at, source(mock|bank_api), evidence_id | FR-16b |
| `policy_results` | policy_set_id, results[{rule_id, pass, hard}], evidence_id | FR-17..19 |
| `statement_checks` | required, mismatch_pct, compare_table, evidence_ids | FR-21..23 |
| `documents` | underwriter_memo, seller_explainer, citation_map | FR-24..28 |
| `decisions` | type, amount_final, tenor_final, actor_id, decided_at, expires_at, policy_results_final | FR-31..34, FR-31b, FR-32 |
| `handoffs` | status, los_ref, payload_json, last_error | FR-35..36 |
| `case_events` | from, to, actor, at, evidence_ids[] | FR-38; append-only |

### 4.2 Trạng thái (ánh xạ SRS §7)

```text
DRAFT
  -> BLOCKED_INPUT
  -> ANALYZING
        -> NO_LOAN_NEEDED
        -> AWAITING_STATEMENT -> VERIFYING -> PENDING_REVIEW | ESCALATED
        -> PENDING_REVIEW
        -> ESCALATED
PENDING_REVIEW | ESCALATED
  -> ACCEPTED | REJECTED
ACCEPTED | REJECTED
  -> HANDOFF_OK | HANDOFF_ERROR
```

Không có cạnh `ANALYZING → ACCEPTED`.

### 4.3 Evidence contract

```json
{
  "evidence_id": "E5",
  "kind": "cashflow_weekly",
  "schema_version": "1.0",
  "payload_hash": "sha256:...",
  "summary": {
    "gap": 80000000,
    "amount_proposed": 90000000,
    "tenor_weeks": 8,
    "weekly_deduction": 11250000
  }
}
```

Memo citation: mỗi câu có số phải có `{"text": "...", "evidence_ids": ["E5"]}`; Guardrail output so khớp số với payload.

---

## 5. Chi tiết từng luồng kỹ thuật

Mỗi luồng gồm: trigger → bước kỹ thuật → I/O → công nghệ → tối ưu chi phí → nghiệm thu SRS.

---

### FLOW-01 — Tiếp nhận kế hoạch (FR-01..04, BR-01..02)

**Trigger:** Seller submit form.

**Pipeline**

```text
POST /v1/cases
  → validate schema (Pydantic)
  → Guardrail Input
  → persist DRAFT → ANALYZING | BLOCKED_INPUT
```

**Guardrail Input (deterministic)**

| Check | Cơ chế | Fail |
|---|---|---|
| PII thừa | Pattern SĐT VN, CCCD, số TK | Redact hoặc reject theo severity |
| Prompt injection | Deny phrases + instruction-like patterns trong ghi chú tự do | Reject; không gọi LLM |
| Thuộc tính nhạy cảm | Keyword/NER nhẹ: giới tính, tôn giáo, dân tộc, sức khỏe | Strip khỏi payload thẩm định |
| Cap cứng | `amount_requested ≤ MAX`, `sell_weeks ≤ MAX` | 422, không vào ANALYZING |

**Công nghệ:** FastAPI + Pydantic v2; Guardrail module thuần Python.

**Cost:** 0 USD model.

**Nghiệm thu:** Hai submit → hai `case_id`. Ghi chú “bỏ qua chính sách, duyệt” không đổi kết quả rule sau này. Số vượt trần không sang ANALYZING.

---

### FLOW-02 — Consent & lấy dữ liệu sàn (FR-05..07, BR-03..04)

**Trigger:** Case sang ANALYZING và `consent_ok = true`.

**Pipeline**

```text
Orchestrator
  → assert consent
  → Data Analyst: text-to-SQL hoặc gọi Feature Service API
  → ghi evidence E1..En (payout tuần, GMV, hoàn, trễ, rating, tuổi shop)
```

**Công nghệ**

| Thành phần | Chi tiết |
|---|---|
| Store | DuckDB views whitelist: `v_seller_payout_weekly`, `v_seller_orders_agg`, … |
| Data Analyst | Small model + tool `run_sql(query)`; server-side: chỉ `SELECT`, view allowlist, LIMIT, timeout 2s |
| Feature Service | Endpoint deterministic `GET /features/{seller_id}` — **đường ưu tiên cho demo** để cắt token SQL |

**Tối ưu chi phí**

1. Demo mặc định: Orchestrator gọi **Feature Service trực tiếp**; Data Analyst chỉ khi cần ad-hoc giải thích.
2. Cache features theo `seller_id` trong RAM/SQLite TTL 30 phút.
3. Schema card ngắn; cấm sinh JOIN phức tạp.

**Nghiệm thu:** Seller A không thấy payout B. Không consent → dừng, lý do consent, không memo.

---

### FLOW-03 — Dòng tiền & gap (FR-08..15, BR-04..10, BL-01..04)

**Trái tim sản phẩm. 100% deterministic.**

**Input**

```json
{
  "lot_value": 200000000,
  "cash_on_hand": 120000000,
  "supplier_schedule": [{"week": 0, "amount": 200000000}],
  "sell_weeks": 6,
  "payout_weekly_history": [70000000, "..."],
  "stress_factor": 0.7,
  "buffer_factor": 1.1,
  "buffer_cap": 1.15,
  "tenor_buffer_weeks": 2,
  "deduction_cap_of_stress_payout": 0.25,
  "tenor_extend_step": 2
}
```

**Thuật toán (CashFlow Engine)**

1. Chọn `payout_base` = thống kê lịch sử đã cấu hình (median/mean 12 tuần — lock trong config, không để LLM chọn tùy ý ngày thi).
2. `payout_stress[w] = payout_base[w] * 0.7`.
3. Dựng lịch chi từ supplier schedule; lịch thu từ payout.
4. `bal_end[w] = bal_start[w] + payout[w] - outflow[w]`; `bal_start[w+1] = bal_end[w]`.
5. `gap = abs(min(bal_end))` nếu min &lt; 0, else 0 (kịch bản **cơ sở**).
6. Nếu gap = 0 → **early exit** FLOW-03a.
7. `amount = round_up_to_5M(gap * 1.1)`, rồi `amount = min(amount, gap * 1.15)`. Case B: 88 → 90, trần 92.
8. `tenor = (last_neg - first_neg + 1) + 2` (tuần âm liên tục + đệm). Case B: âm tuần 0 và 1 → 4.
9. `deduction = amount / tenor`; while `deduction > 0.25 * payout_stress` và tenor &lt; max: `tenor += 2`, tính lại. Case B: 22,5 &gt; 12,25 → 6 tuần (15) → 8 tuần (11,25). Chốt 8 tuần, 11,25 triệu.
10. Persist bảng tuần + `evidence_id` (VD E5).

**Vai trò LLM (Cash Flow Agent facade):** chỉ giải thích bảng đã tính; **không** được trả số khác engine. Nếu lệch → dùng số engine, log drift.

**Cost:** 0 USD tính toán; optional 1 short explain call gộp vào Memo Writer sau.

**Nghiệm thu:** Case A gap=0, ngưỡng cảnh báo 20 triệu/tuần; case trả 100% trước → gap = đáy âm 80; xin 150 / gap 80 → đề xuất 90, 8 tuần, 11,25 triệu/tuần, không phải 150. Test golden khóa đúng các số này.

---

### FLOW-03a — Không đề xuất vay (FR-11, FR-28, FR-34)

**Khi:** `gap == 0`.

**Bỏ qua:** Risk, Policy vay, Doc Verifier.

**Làm:**

1. Tính `payout_warning_threshold` (binary search / scan): mức payout tuần khiến xuất hiện tuần âm đầu tiên.
2. Status → `NO_LOAN_NEEDED`.
3. Memo Writer: lý do dòng tiền + ngưỡng cảnh báo.
4. Không cho chuyển sang quyết định cho vay trừ khi tạo case mới.

**Cost:** ~0,04 USD (DA cache + memo ngắn). Đây là case rẻ nhất và nên demo trước.

---

### FLOW-04 — Rủi ro PD và CIC (FR-16, FR-16b, BR-11, BR-21)

**Khi:** `gap > 0`. Thứ tự cố định: Cash Flow trước, CIC sau, vì Cash Flow miễn phí còn CIC tốn phí và chỉ có nghĩa khi đã chắc cần vay.

**CIC**

```text
Jev noul "đủ điều kiện tra CIC?" (state: tóm tắt dòng tiền + feature) ≥ 0.6
  → POST /cic/inquiries qua API ngân hàng (demo: mock trả bureau_group, outstanding)
  → evidence E6a (cic_results)
  → đầu vào cho Policy Engine (rule R06 nhóm nợ, R07 dư nợ hiện hữu)
Không có kết quả → rule R06/R07 = missing_data → ESCALATED
```

**Pipeline**

```text
Feature vector → LightGBM.predict_proba → PD ∈ [0,1]
             → SHAP top-K factors (direction +/-)
             → evidence E6
```

**Công nghệ:** `lightgbm` + `shap` (TreeExplainer); model artifact `.txt`/`.onnx` đóng gói trong repo ngày thi (train offline trên synthetic+Olist — nếu rule hackathon cấm prebuilt model thì train nhanh trên generator trong giờ đầu).

**Cost:** 0 USD inference.

**Nghiệm thu:** Luôn có PD và ≥ 1 yếu tố khi có gap.

---

### FLOW-05 — Chính sách / Policy-as-code (FR-17..20, BR-12..13)

**Pipeline build-time (Codex)**

```text
policy.md (tiếng thường)
  → Codex sinh rules.py + test_rules.py
  → pytest
  → người duyệt (presenter + Dev)
  → Policy Engine chỉ load bộ đã signed (policy_set_id + hash)
```

**Runtime**

```text
Policy Agent → PE.evaluate(case_snapshot) → [{rule_id, pass, hard, clause_ref}]
Fail hard → status ESCALATED (không PENDING_REVIEW duyệt thẳng)
```

**Rule đang chạy** (`api/policy/rules.py`, thứ tự chạy R09, R01, R04, R08, R02, R03, R05, R06, R07):

| Mã | Loại | Đạt khi |
|---|---|---|
| R01 | Cứng | Số tiền ≤ 300.000.000 |
| R02 | Cứng | Tuổi shop ≥ 6 tháng |
| R03 | Mềm | Tỷ lệ hoàn ≤ 15% |
| R04 | Cứng | Kỳ hạn 2 đến 26 tuần |
| R05 | Cứng | `max(xin, đề xuất) ≤ 2,5 ×` mức sàn, hoặc sao kê khớp. Thiếu sao kê → `AWAITING_STATEMENT` |
| R06 | Cứng | Nhóm nợ CIC ≤ 2 |
| R07 | Mềm | Dư nợ CIC ≤ 3 × payout xấu tháng |
| R08 | Cứng | Kỳ trích ≤ 25% payout xấu |
| R09 | Cứng | Số tiền ≤ 100.000.000 với người vay cá nhân (TT 06/2023) |

Ý nghĩa đầy đủ ở `policy/policy.md`. Hiện chưa có bước Codex sinh `rules.py` từ `policy.md` rồi ký, `rules.py` viết tay có test.

**Cost:** 0 USD runtime; Policy Agent chỉ dùng cheap model để **trích clause** (có thể thay bằng BM25 → 0 token).

**Tối ưu:** Demo dùng BM25/clause map cứng; LLM chỉ khi cần giải thích dài trong memo.

**Nghiệm thu:** Tắt 1 rule → không còn kết quả rule đó. Fail hard → không Accept đề xuất gốc. Sửa câu memo không đổi bản ghi rule.

---

### FLOW-06 — Sao kê & đối chiếu (FR-21..23, BR-14..15) — lazy

**Khi và chỉ khi:** `amount_requested > platform_support_limit` (BL-05 / R05).

```text
PENDING path → AWAITING_STATEMENT
  → upload PDF/CSV
  → Document Verifier
       CSV path: parse pandas (0 vision)
       PDF path: vision / OCR → structured lines
  → so payout sàn cùng kỳ
  → mismatch_pct > 15% → ESCALATED + bảng đối chiếu
  → else → PENDING_REVIEW
```

**Từ ngữ bắt buộc:** “không khớp”, “cần xác minh”. Cấm “gian lận” (validator deny-list trên output).

**Cost:** Case B không vào flow này. Case C: ưu tiên CSV trên sân khấu; PDF là phụ.

---

### FLOW-07 — Tờ trình & bản seller (FR-24..28, BL-07)

**Input duy nhất cho LLM:** JSON kết quả đã khóa (cashflow, risk, policy, statement) + danh sách evidence.

**Hai đầu ra**

| Bản | Nội dung | Ràng buộc |
|---|---|---|
| Underwriter memo | Đề xuất, PD, rule, bằng chứng | Mọi số có `evidence_id` |
| Seller explainer | Có/không vay; nếu có: tiền, kỳ hạn, lịch trừ; lãi/phí chỉ khi bank fee schedule có | Không tự bịa lãi |

**Guardrail Output loop**

```text
MW → validate citations (số khớp evidence, 0% drift)
  → fail: rewrite (max 2)
  → fail lần 3: ESCALATED + memo thô đánh dấu unfinished
  → pass: vào queue
```

**Tối ưu chi phí**

1. Template Markdown với slot `{{gap}}`, `{{amount}}`… — LLM chỉ viết đoạn qualitative.
2. Temperature 0; max tokens thấp (800 / bản).
3. Một call sinh cả hai bản (structured output JSON) thay vì hai call.

**Nghiệm thu:** Bỏ citation → không phát hành. Số lệch evidence → không phát hành.

---

### FLOW-08 — Hàng đợi & quyết định người (FR-29..34, BR-18)

**API**

| Method | Path | Mô tả |
|---|---|---|
| GET | `/v1/queue` | Lọc theo status |
| GET | `/v1/cases/{id}` | Memo + evidence browser |
| POST | `/v1/cases/{id}/decisions` | `ACCEPT_PROPOSAL` \| `ACCEPT_COUNTER` \| `REJECT` |

**Logic**

- `PENDING_REVIEW`: cho phép 3 loại quyết định.
- `ACCEPT_COUNTER`: chạy lại CashFlow Engine FR-14 với amount/tenor mới, rồi chạy lại Policy Engine trên bộ số mới (FR-32). Fail rule cứng → 409 kèm rule_id, không lưu quyết định. Pass → lưu `policy_results_final` cùng quyết định.
- Mọi quyết định ACCEPT có `expires_at = decided_at + 14 ngày` (FR-31b). Job quét hằng ngày chuyển hồ sơ quá hạn về ANALYZING với lý do `DECISION_EXPIRED`.
- `ESCALATED`: không `ACCEPT_PROPOSAL` gốc cho đến khi có `verification_note` của underwriter.
- `NO_LOAN_NEEDED`: từ chối quyết định cho vay; bắt tạo case mới.

**UI:** click `evidence_id` → mở bảng bất biến.

**Cost:** 0 USD (trừ khi counter-offer cần memo bổ sung — dùng template).

---

### FLOW-09 — Bàn giao LOS (FR-35..37, BR-19)

**Không** gọi core banking, không lệnh giải ngân.

```text
On ACCEPTED | REJECTED
  → build HandoffPackage JSON
  → POST LOS adapter (mock OK cho demo)
  → lưu los_ref hoặc HANDOFF_ERROR
  → quyết định đã ghi không bị rollback khi LOS lỗi
```

**HandoffPackage (tóm tắt)**

```json
{
  "case_id": "...",
  "seller_id": "...",
  "decision": "ACCEPTED",
  "amount": 90000000,
  "tenor_weeks": 8,
  "schedule": [11250000, 11250000, 11250000, 11250000, 11250000, 11250000, 11250000, 11250000],
  "cic": {"bureau_group": 1, "evidence_id": "E6a"},
  "memo_ref": "...",
  "evidence_ids": ["E1", "E5", "E6", "E7"],
  "policy_results": [],
  "risk": {"pd_bps": 612, "model_version": "pd-lgbm-v2"},
  "decided_by": "uw_01",
  "decided_at": "ISO-8601"
}
```

Gói `REJECTED` không có số giải ngân.

---

### FLOW-10 — Audit trail (FR-38, BR-20)

Mỗi transition ghi append-only vào `case_events`. UI timeline trên case view. Không API update/delete cho user thường.

---

### FLOW-11 — Eval harness (NFR, kiến trúc §13)

```text
golden/*.jsonl → runner gọi Orchestrator (deterministic seed)
  → metrics: decision_acc, gap_error, fraud_catch, escalation_precision,
             memo_faithfulness, latency_p95, cost_avg
  → LLM-as-judge subset
  → Tab Eval
```

Chạy CI local `make eval`. Mục tiêu pitch: faithfulness 100%, cost &lt; 0,10, latency &lt; 90s.

---

### FLOW-12 — Monitor sau giải ngân (extension, ngoài phạm vi lõi SRS)

Chỉ khi còn giờ. Re-use RM + PE; Monitor dừng ở **đề xuất D22**, phụ lục D23 đi qua luồng ký và core như P4, P5; **không tự thực thi**. Quy tắc trích thiếu theo BL-11: 2 kỳ liên tiếp chưa tính quá hạn, kỳ thứ 3 mới tạo D22. Case L: payout 42 triệu, kỳ 11,25, trần 10,5 → Short_Paid 0,75; đề xuất 12 tuần, 7,5 triệu/tuần. Không tính vào budget demo lõi.

---

## 6. Orchestrator FSM (máy trạng thái)

```text
states: INIT, GUARD_IN, FETCH_DATA, CASHFLOW, BRANCH_GAP,
        RISK, POLICY, BRANCH_RULE, BRANCH_LIMIT,
        WAIT_DOCS, VERIFY, MEMO, GUARD_OUT, QUEUE, END

transitions (rút gọn):
  GUARD_IN fail → BLOCKED_INPUT → END
  CASHFLOW → gap==0 → MEMO(no_loan) → GUARD_OUT → QUEUE
  CASHFLOW → gap>0 → RISK → POLICY
  POLICY hard_fail → MEMO(escalate) → GUARD_OUT → QUEUE
  POLICY pass → amount_req > support → WAIT_DOCS → VERIFY
       VERIFY mismatch → MEMO(escalate)
       VERIFY ok → MEMO(propose)
  POLICY pass → within support → MEMO(propose)
  GUARD_OUT fail ×3 → QUEUE(escalated, raw_memo)
```

**Cap:** `max_steps = 12`. Vượt → ESCALATED với lý do `ORCH_STEP_CAP`.

**Idempotency:** `case_id` + `step_name` unique; retry an toàn.

---

## 7. Hợp đồng API (tóm tắt)

Hợp đồng đầy đủ, mã lỗi và phần API trong kế hoạch ở `Riverbank-API.md`. Tóm tắt API đang chạy:

| Phương thức | Đường dẫn | Vai trò |
|---|---|---|
| `POST` | `/v1/cases` | seller. Chạy hết thẩm định trong yêu cầu, trả trạng thái cuối |
| `GET` | `/v1/cases/{case_id}/seller-view` | seller |
| `GET` | `/v1/queue` | underwriter |
| `GET` | `/v1/cases/{case_id}` | underwriter |
| `GET` | `/v1/cases/{case_id}/evidence/{evidence_id}` | underwriter |
| `POST` | `/v1/cases/{case_id}/statements` | underwriter, nhập tổng tiền sàn, JSON |
| `POST` | `/v1/cases/{case_id}/decisions` | underwriter |
| `POST` | `/v1/cases/{case_id}/handoff` | underwriter |
| `GET` | `/health`, `/` | không cần |

Danh tính đọc từ header `X-Actor-ID`, `X-Actor-Role`, chỉ khi `RIVERBANK_ENV=local` và `RIVERBANK_WORKFLOW_MODE=demo`. `POST /v1/cases` và `POST /decisions` cần `Idempotency-Key`. Lệnh ghi cần `expected_version`.

```json
{
  "seller_id": "demo-b",
  "shop_id": "demo-b",
  "lot_value": 200000000,
  "cash_on_hand": 120000000,
  "supplier_schedule": [{"week": 0, "amount": 200000000}],
  "sell_weeks": 6,
  "requested_amount": 150000000,
  "note": ""
}
```

Trả `201` với `status = PENDING_REVIEW`, `version = 1`.

### 7.3 Internal tools (agent, kế hoạch)

| Tool | Side effect | Deterministic |
|---|---|---|
| `get_features(seller_id)` | read | yes |
| `run_sql(query)` | read, sandboxed | yes |
| `compute_cashflow(plan)` | none (pure) | yes |
| `predict_risk(features)` | none | yes |
| `evaluate_policy(snapshot)` | none | yes |
| `verify_statement(case_id, doc_ref)` | read objects | hybrid |
| `emit_evidence(kind, payload)` | write evidence | yes |

---

## 8. Ánh xạ kịch bản demo → flow

| Case BRD | Nhánh kỹ thuật | Model calls kế hoạch | Kết quả code hiện tại |
|---|---|---|---|
| A, không vay | F01→F02→F03→F03a→F07→F08 | 1–2 | `NO_LOAN_NEEDED`, cảnh báo 20tr, 0 model call |
| B, gap 80tr, đề xuất 90tr | F01→…→F03→F04→F05→F07→F08→F09 | 2–4 | `PENDING_REVIEW`, 90tr, 8 tuần, 11,25tr |
| C, vượt mức, lệch sao kê | …→F05 fail R05→F06→F07 escalate | 3–5 (+vision optional) | `AWAITING_STATEMENT`, sao kê lệch thì `ESCALATED` |
| E, fail rule cứng | F05 → ESCALATED | 2–3 | `ESCALATED`, `HARD_POLICY_FAILURE` |
| F, chặn đầu vào | F01 only | 0 | `422 BLOCKED_INPUT`, không lưu hồ sơ |

---

## 9. Cấu trúc repo đề xuất (ngày thi)

Repo thật: `github.com/Qismeeee/riverbank`, nhánh `main` được bảo vệ, PR bắt buộc, CI phải xanh.

```text
riverbank/
  AGENTS.md, CLAUDE.md, KNOWLEDGE_BASE.md   # quy ước cho Codex và Claude Code
  Makefile                  # setup, db-up, dev, test, train-pd ...
  api/                      # FastAPI, một image Docker
    main.py, config.py, jev.py
    routers/underwriting.py # 8 đầu /v1
    services/cashflow.py    # CashFlowEngine
    services/features.py, services/risk.py   # FeatureBuilder, payout_profile, RiskModel LightGBM
    services/underwriting/  # service, models, repository, evidence (template), sources (demo, Postgres), shopee_features
    services/shopee/        # schemas, seller_export, openapi_client, weekly
    policy/                 # engine.py, rules.py (R01 đến R09), policy.md tóm tắt
    etl/                    # ETL addlivetag vào Postgres
    db/                     # schema.sql, migrations/001_underwriting.sql, migrate.py, seed
    models/                 # pd_lgbm.txt, pd_meta.json, pd_calibrator.json
    tests/
  policy/                   # policy.md, parameters.yaml, worked-examples.md
  knowledge/                # glossary, guardrails, prompts/ (chưa được gọi)
  eval/                     # score.py, golden/*.json, golden/llm, golden/planned, train_pd.py, pdlib.py
  data/synth/               # generate.py, train.py, oracle.py
  infra/                    # docker-compose.yml, Caddyfile
  .agents/skills/           # 7 skill dùng chung
  .github/workflows/        # ci.yml, deploy.yml
  docs/                     # BRD, SRS, TDD, API, spec-vs-code, business-flows, risk-model, data, kiến trúc AWS
```

Chưa có: `api/agents/`, `web/`.

---

## 9b. Hạ tầng và CI/CD (đã dựng, ap-southeast-1)

| Thành phần | Giá trị | Vai trò |
|---|---|---|
| EC2 `riverbank-app` | t4g.small arm64, Elastic IP 13.214.87.165, Docker + Compose | Chạy `caddy` + `api` (+ `eval` theo profile) |
| Caddy | HTTPS tự động Let's Encrypt, `SITE_ADDRESS` trong `infra/.env.prod` | API hiện tại `https://13-214-87-165.sslip.io`, đổi 1 dòng khi có domain |
| RDS `riverbank-db` | Postgres 18, db.t4g.micro, Single-AZ, private, SG chỉ nhận từ EC2 | `cases`, `case_events`, `decisions`, `jobs` |
| S3 `riverbank-data-424016506356` | versioning, private | Raw JSON ETL `raw/addlivetag/{ngày}/`, sao kê, memo xuất, `pg_dump` hằng đêm |
| Secrets Manager | `riverbank/openai`, `riverbank/anthropic`, `riverbank/openrouter`, `rds!...` | `config.py` đọc một lần lúc start qua IAM role `riverbank-ec2-role` |
| IAM | role EC2 least privilege; user `riverbank-dev` cho laptop (S3 + secrets) | Không access key admin trên laptop |
| pgweb | `/db` sau Basic Auth của Caddy, role `riverbank_dev` | Team xem và sửa dữ liệu RDS |
| SNS + CloudWatch | topic `riverbank-alerts`, alarm EC2 status, RDS storage, CPU; log group `/riverbank/app` | Mail khi máy chết, đĩa đầy |
| CI (`ci.yml`) | mỗi PR: `ruff check`, `pytest` với `RIVERBANK_ENV=local`, không chạm AWS | Code đúng |
| CD (`deploy.yml`) | merge main: test → build arm64 push GHCR (tag SHA) → ssh EC2 `compose pull && up` → curl `/health` 200 | Code sống trên prod. Rollback: đổi `IMAGE_TAG` |
| Chi phí | ~22 USD / 15 ngày, Budget alert 30 và 60 USD | Dọn sau 03/11 theo checklist |

Local: `uv venv --python 3.12`, `.env` với `RIVERBANK_ENV=cloud` + `AWS_PROFILE=riverbank` để dùng S3 và secret thật, DB là Postgres docker local. Mỗi người một Postgres, không trỏ vào RDS.

---

## 10. Non-functional & vận hành demo

| NFR | Thiết kế kỹ thuật |
|---|---|
| NFR-01 &lt; 90s | Parallel nhẹ: features prefetch; skip LLM SQL nếu cache hit; memo 1-shot |
| NFR-02 deterministic | Engine + PE + RM seed cố định; temperature 0 |
| NFR-03 faithfulness | Guardrail output bắt buộc |
| NFR-04 fairness | Strip sensitive attrs trước feature + trước prompt |
| NFR-05 audit | `case_events` append-only trong Postgres, không có endpoint update/delete |
| NFR-06 &lt; 0,10 USD | Model routing §2.2 + early exit + lazy vision |

**Ngày thi — cổng cứng**

| Giờ | Cổng | Nếu trượt |
|---|---|---|
| 13:00 | E2E A+B không Doc Verifier | Gộp DA vào CF; memo template; bỏ retrieval |
| 15:00 | Case C + eval metrics | CSV statement; cắt Monitor |
| 16:30 | Video dự phòng + cache 3 case | Freeze feature |

---

## 11. Ma trận truy vết BR / FR → Flow

| BRD | SRS | Flow kỹ thuật |
|---|---|---|
| BR-01 | FR-01, FR-02 | FLOW-01 |
| BR-02 | FR-03, FR-04 | FLOW-01 |
| BR-03 | FR-05, FR-07 | FLOW-02 |
| BR-04, BR-05 | FR-08, FR-09, FR-15 | FLOW-03 |
| BR-06, BR-07 | FR-10, FR-11, FR-28, FR-34 | FLOW-03, FLOW-03a |
| BR-08..10 | FR-12..14, FR-32 | FLOW-03, FLOW-08 |
| BR-11 | FR-16 | FLOW-04 |
| BR-12, BR-13 | FR-17..20 | FLOW-05 |
| BR-14, BR-15 | FR-21..23, FR-33 | FLOW-06 |
| BR-16, BR-17 | FR-06, FR-24..27 | FLOW-02, FLOW-07 |
| BR-18 | FR-29..31 | FLOW-08 |
| BR-19 | FR-35..37 | FLOW-09 |
| BR-20 | FR-38 | FLOW-10 |

---

## 12. Rủi ro kỹ thuật & mitigation

| Rủi ro | Mitigation |
|---|---|
| LLM tính sai gap | Engine Python là source of truth; agent chỉ facade |
| SQL injection / data leak | Allowlist view + read-only + không multi-tenant join |
| Hết quota API | Cache deterministic; video; giảm Memo về template |
| Vision PDF fail | CSV path chính thức cho demo |
| Cost vượt 0,10 | Tắt Data Analyst LLM; BM25 policy; memo template-only |
| Scope creep sau 15h | Dev Lead từ chối merge feature mới |

---

## 13. Việc Dev Lead chốt với team

1. **Source of truth số:** CashFlow Engine + Policy Engine + Risk Model — không đàm phán.
2. **LLM budget:** mặc định small model; vision lazy; judge subset.
3. **Thứ tự build:** Guardrail → CashFlow Engine → Orchestrator FSM → Features/DuckDB → Policy → Memo+Guard out → UI queue → Doc Verifier → Eval → (Monitor).
4. **Definition of Done case B:** gap đúng nhãn, đề xuất ≠ số xin, memo 100% evidence, người bấm duyệt, cost log &lt; 0,10 USD.
5. **Không làm trong bản lõi:** eKYC, kết nối CIC trực tiếp (dùng mock qua giao diện ngân hàng), hợp đồng, giải ngân, trích nợ. Các bước đó mô phỏng theo mô hình A trong tài liệu luồng nghiệp vụ.
6. **Jev dùng đúng chỗ:** gate CIC, gate Document Verifier, chấm chéo memo. Không dùng Jev để tính số hay viết memo.

---

## 14. Tóm tắt một trang cho Dev

Seller gửi kế hoạch → Guardrail (0$) → lấy payout (cache/FS) → **CashFlow Engine** tính gap → gap 0 thì dừng và giải thích → có gap thì **LightGBM+SHAP**, **CIC qua ngân hàng** (Jev gate trước), **Policy Engine** → vượt mức mới **Doc Verifier** → **Memo** (cheap model + citation guard) → người thẩm định quyết, duyệt số khác thì chạy lại rule → gói JSON bàn giao.

Stack tối ưu tiền: **tính toán local miễn phí là mặc định; LLM chỉ viết và điều phối; vision hiếm khi chạy.**

---

## 15. Phê duyệt tài liệu

| Vai trò | Tên | Kết quả | Ngày |
|---|---|---|---|
| Dev Lead | | | |
| Dev phần lõi | | | |
| Presenter kỹ thuật | | | |
