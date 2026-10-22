# AGENTS.md

Hướng dẫn cho Codex, Claude Code và người mới vào repo. Đọc file này trước khi sửa code.

## Sản phẩm

Riverbank: cho vay vốn lưu động cho seller Shopee dựa trên dòng tiền payout.
Tài liệu chuẩn nằm trong `docs/`: BRD (nghiệp vụ), SRS (yêu cầu), TDD (thiết kế), business-flows.html (luồng LOS và core).
Khi code mâu thuẫn với docs thì docs thắng. Khi docs mâu thuẫn nhau thì TDD thắng, rồi báo lại trong PR.

## Ba nguyên tắc không được phá

1. Code tính, LLM viết. Gap, số tiền, kỳ hạn, kỳ trích chỉ ra từ `CashFlowEngine` (`api/services/cashflow.py`). Rule chỉ ra từ `PolicyEngine` (`api/policy/`). LLM diễn giải, không được trả số khác.
2. Mọi câu trong memo có `evidence_id` trỏ tới dữ liệu có thật. Không có evidence thì không viết.
3. AI không tự quyết. Hard rule fail thì Escalated, người duyệt không ghi đè. Duyệt số khác phải chạy lại Policy Engine.

## Số vàng (test khóa, đổi là CI đỏ)

Case B: gap 80 triệu, đề xuất 90 triệu, 8 tuần, 11,25 triệu/tuần, payout xấu 49 triệu.
Case A: gap 0, cảnh báo khi payout tuần dưới 20 triệu. Xem `eval/golden/`.

## Cấu trúc

```
api/            FastAPI. main.py, config.py (secrets), jev.py (Jev client)
api/services/   engine code thuần: cashflow.py; shopee/ (schemas, weekly, seller_export, openapi_client)
api/policy/     policy.md là nguồn, engine.py là PolicyEngine, rules.py là bộ rule, test_rules.py là bằng chứng
api/etl/        RawStore, Extractor, Transformer, Loader, Pipeline; bản addlivetag + PostgresLoader
api/db/         schema.sql, một DB Postgres cho feature lẫn trạng thái app
api/routers/    HTTP endpoint, mỏng, gọi services
api/agents/     orchestrator, data_analyst, memo_writer, doc_verifier, monitor (LLM facade)
api/tests/      pytest, chạy không cần AWS, không cần mạng
eval/golden/    case chuẩn, test_golden.py đọc tự động
data/synth/     generate.py (shop demo theo schema Shopee), train.py (data huấn luyện có nhãn), calibration.json (60 shop thật); out/ và train/ không commit
web/            Next.js, deploy Vercel
infra/          docker-compose.yml, Caddyfile, .env.prod cho EC2
.agents/skills/ skill cho Codex và Claude Code, đọc skill đúng việc trước khi code
```

## Lệnh

```
make setup      tạo venv Python 3.12 bằng uv, cài dev deps, cài pre-commit
make db-up      Postgres local trong docker
make dev        uvicorn reload tại :8000
make test       ruff check + ruff format --check + pytest
make fmt        ruff format
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
