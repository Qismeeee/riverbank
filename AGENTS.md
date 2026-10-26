# AGENTS.md

Hướng dẫn cho Codex, Claude Code và người mới vào repo. Đọc file này trước khi sửa code.

## Sản phẩm

Riverbank: cho vay vốn lưu động cho seller Shopee dựa trên dòng tiền payout.
Tài liệu chuẩn nằm trong `docs/`: BRD (nghiệp vụ), SRS (yêu cầu), TDD (thiết kế), business-flows.html (luồng LOS và core).
Số và rule: code đang chạy là chuẩn (chốt 23/10/2026). `policy/parameters.yaml` là bản chép của `CashFlowConfig` và `PolicyConfig`, có test kiểm khớp. Đổi ngưỡng thì đổi code, `parameters.yaml`, `policy/policy.md` trong cùng PR.
Hành vi chưa có code (agent LLM, Monitor) thì docs là kế hoạch. Danh sách lệch ở `docs/Riverbank-spec-vs-code.md`.

## Ba nguyên tắc không được phá

1. Code tính, LLM viết. Gap, số tiền, kỳ hạn, kỳ trích chỉ ra từ `CashFlowEngine` (`api/services/cashflow.py`). Rule chỉ ra từ `PolicyEngine` (`api/policy/`). LLM diễn giải, không được trả số khác.
2. Mọi câu trong memo có `evidence_id` trỏ tới dữ liệu có thật. Không có evidence thì không viết.
3. AI không tự quyết. Hard rule fail thì Escalated, người duyệt không ghi đè. Duyệt số khác phải chạy lại Policy Engine.

## Số vàng (test khóa, đổi là CI đỏ)

Nhãn `eval/golden/` theo `policy/worked-examples.md`. `test_golden.py` chạy từng nhãn qua `UnderwritingService` thật.
FX-B: gap 80 triệu, đề xuất 90 triệu (88 làm tròn lên bội 5 triệu), 8 tuần, trích 11,25 triệu/tuần, payout xấu 49 triệu.
FX-A: gap 0, ngưỡng cảnh báo 20 triệu/tuần.

## Cấu trúc

```
api/            FastAPI. main.py, config.py (secrets), jev.py (Jev client), llm.py (LlmClient, một cửa gọi OpenAI hoặc OpenRouter, cache khi eval), prompts.py (nạp knowledge/prompts)
api/services/   engine code thuần: cashflow.py, features.py (FeatureBuilder, một nguồn feature cho train và serving), risk.py (RiskModel từ api/models/); shopee/ (schemas, weekly, seller_export, openapi_client)
api/policy/     engine.py là PolicyEngine, rules.py là bộ rule R01 đến R10, policy.md tóm tắt; bản đầy đủ ở policy/policy.md
api/etl/        RawStore, Extractor, Transformer, Loader, Pipeline; bản addlivetag + PostgresLoader
api/db/         schema.sql, một DB Postgres cho feature lẫn trạng thái app
api/routers/    HTTP endpoint, mỏng, gọi services
api/services/underwriting/  luồng hồ sơ: service.py, models.py, repository.py, evidence.py (evidence và template câu), memo.py (MemoWriter template hoặc LLM, MemoValidator, MemoComposer), sources.py, cic.py (SimulatedCicSource đọc api/data/cic_profiles.json, báo cáo nhiều khoản vay, nhóm nợ 12 tháng)
api/services/statements/    đọc sao kê: documents.py (nhận loại file, đọc số VND, ngày), parsers.py (CSV, Excel xlsx và xls, PDF kẻ bảng, PDF theo vị trí chữ), vision.py (OCR qua LlmClient, chỉ trang ảnh, đọc lại khi lệch số dư hoặc lệch tổng), images.py (xoay EXIF, HEIC và WEBP sang JPEG, thu nhỏ), balance.py (kiểm số dư từng dòng và tổng đầu cuối kỳ), storage.py (lưu file gốc: none hoặc S3), classifiers.py (dòng tiền sàn: regex lọc rồi Jev quyết), reader.py (đọc, che PII, cộng tổng), factory.py
api/services/copilot/      extractor.py (chat ra bản nháp kế hoạch, nhớ `known`), replies.py (câu chat do code ghép), advisor.py (hỏi đáp trên số đã khóa, câu mẫu khi LLM lỗi), history.py (lưu lượt chat theo hồ sơ, memory hoặc Postgres), grounding.py, screening.py (Jev chặn câu hỏi)
api/services/jobs.py        hàng đợi job (memory hoặc Postgres SKIP LOCKED, lease, thử lại lỗi 5xx) và JobWorker. Handler nộp sao kê ở underwriting/uploads.py, worker bật trong lifespan của main.py
api/services/guard.py       OutputGuard: số phải có trong bằng chứng, từ cấm, câu nói ngược trạng thái. Dùng cho memo, eval và Copilot
api/tests/      pytest, chạy không cần AWS, không cần mạng
eval/golden/    case chuẩn, test_golden.py đọc tự động
eval/pdlib.py, train_pd.py, train_pd.ipynb   metric, trainer, calibration dùng chung; train headless; notebook đầy đủ (xem docs/Riverbank-risk-model.md)
data/synth/     generate.py (shop demo theo schema Shopee), train.py (data huấn luyện có nhãn), oracle.py (PD thật của tập test, trần AUC), calibration.json (60 shop thật); out/, train/, test/ không commit
web/            Next.js, deploy Vercel (chưa có trong repo)
infra/          docker-compose.yml, Caddyfile, .env.prod cho EC2
.agents/skills/ skill cho Codex và Claude Code, đọc skill đúng việc trước khi code
```

## Lệnh

```
make setup      tạo venv Python 3.12 bằng uv, cài dev deps, cài pre-commit
make db-up      Postgres local trong docker
make dev        uvicorn reload tại :8000
make test       ruff check + ruff format --check (api, eval, data/synth) + pytest
make fmt        ruff format
make eval-report  sinh lại api/studio/eval_report.json từ eval/golden (Studio API đọc)
make memo-llm   chạy Memo Writer LLM thật trên golden case, cần key trong .env, có cache ở eval/.cache/llm
```

Chỉ dùng `make test` để xác nhận trước khi mở PR. CI chạy đúng lệnh đó.

## Quy ước code

Python 3.12, ruff line 100, type hint ở hàm public, dataclass cho input và output của engine. Không docstring đầu file, không comment nhắc lại code; tên hàm và test là tài liệu. Điểm mở rộng là lớp cơ sở (`ShopeeSource`, `Extractor`, `Transformer`, `Loader`, `RawStore`), thêm bản mới bằng kế thừa, không sửa bản cũ.
Tiền là `int` VND, không float. Tuần là `int` từ 0. Không dùng `datetime.now()` trong engine, truyền thời gian vào.
Không đọc biến môi trường ngoài `api/config.py`. Không gọi mạng trong test. LLM call đi qua một chỗ, có cache theo hash input khi chạy eval.
Endpoint mới phải có test. Rule mới phải có dòng trong `policy.md` và test. Engine đổi thì golden phải cập nhật kèm lý do trong PR.

## Dữ liệu

Shopee OpenAPI chưa dùng được (cần ISV hoặc Preferred Seller, xem `docs/Riverbank-data.md`). Demo chạy trên shop thật từ `api/etl/` (quy mô thật, payout mô phỏng), synthetic `data/synth/generate.py` và file xuất Kênh Người Bán. Không crawl shopee.vn.

## Secrets

Local đọc `.env` (không commit). Prod đọc AWS Secrets Manager qua IAM role. Không bao giờ dán key vào code, test, log, PR, hay chat.
Thấy key trong diff thì dừng và báo.

## Skills

| Việc | Đọc |
|---|---|
| Sửa hoặc thêm vào CashFlow Engine | `.agents/skills/cashflow-engine/SKILL.md` |
| Thêm rule chính sách | `.agents/skills/policy-rules/SKILL.md` |
| Quyết định có/không, phân loại, chấm điểm bằng Jev | `.agents/skills/jev-decisions/SKILL.md` |
| Memo, citation, evidence | `.agents/skills/memo-evidence/SKILL.md` |
| Thêm golden case, chạy eval | `.agents/skills/eval-golden/SKILL.md` |
| Deploy, đọc log, rollback, secrets | `.agents/skills/deploy-ops/SKILL.md` |
| Dữ liệu seller: file xuất, OpenAPI, synthetic | `.agents/skills/shopee-data/SKILL.md` |

## PR

Nhánh `feat/`, `fix/`, `docs/` từ `main`. PR nhỏ, một việc. Mô tả theo `.github/PULL_REQUEST_TEMPLATE.md`.
CI xanh mới merge. Merge `main` là deploy thật lên EC2, nên không merge thứ chưa chạy local.
