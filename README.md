# Riverbank

Cho vay vốn lưu động cho seller Shopee dựa trên dòng tiền payout. Sea x OpenAI Codex Hackathon Vietnam 2026.

Bắt đầu từ `AGENTS.md` (quy ước, nguyên tắc, skill). Tài liệu nghiệp vụ và thiết kế trong `docs/`.

## Cấu trúc

```
docs/           BRD, SRS, TDD, business-flows.html. Nguồn chuẩn, code phải khớp.
api/            FastAPI. services/ (engine code thuần), policy/ (rule), tests/
eval/           golden case, test_golden.py đọc tự động
data/synth/     sinh dữ liệu seller synthetic theo schema Shopee (python data/synth/generate.py)
web/            Next.js, deploy Vercel, gọi API qua HTTPS
infra/          docker-compose.yml, Caddyfile, .env.prod cho EC2; docker-compose.local.yml cho máy dev
.agents/skills/ skill cho Codex và Claude Code
.github/        ci.yml (mỗi PR), deploy.yml (merge main = deploy EC2)
```

## Chạy local

Cần `uv`, `docker`, `make`. WSL2 Ubuntu là được.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env   # một lần
make setup       # venv Python 3.12, deps, pre-commit, tạo .env từ .env.example
# điền key vào .env, hoặc RIVERBANK_ENV=cloud + AWS_PROFILE=riverbank để đọc Secrets Manager
make db-up       # Postgres local
make dev         # http://localhost:8000/docs
make test        # ruff + pytest, đúng lệnh CI chạy
make jev         # thử Jev, cần OPENROUTER_API_KEY
make etl         # kéo dữ liệu công khai -> S3 raw -> Postgres (ADDLIVETAG_API_KEY trong .env)
make export      # CSV các bảng feature -> data/exports và S3 exports/
make train-data  # sinh data huấn luyện risk model (nhãn mô phỏng, xem docs/Riverbank-risk-model.md)
```

## Quy trình

1. Nhánh từ `main`: `git checkout -b feat/ten-viec`.
2. `make test` xanh, commit (pre-commit tự chạy ruff và chặn key).
3. Push, mở PR theo template. CI chạy ruff + pytest.
4. Merge khi CI xanh. `deploy.yml` build image arm64, push GHCR, ssh EC2, `compose up`, gọi `/health`.
5. Không push thẳng `main`, không force push. Ruleset chặn.

## Dữ liệu

Shopee OpenAPI cần tài khoản ISV hoặc Preferred Seller nên chưa dùng trong hackathon. Demo dùng shop thật qua ETL (`make etl`, nguồn data.addlivetag.com), synthetic (`make synth`) và file seller xuất từ Kênh Người Bán. Chi tiết `docs/Riverbank-data.md`.

## Số vàng

Case B: gap 80 triệu, đề xuất 90 triệu, 8 tuần, 11,25 triệu/tuần. Case A: gap 0, cảnh báo payout dưới 20 triệu/tuần.
Test khóa các số này. Đổi engine thì đổi docs và `eval/golden/` cùng PR.

## Hạ tầng (ap-southeast-1)

| Thành phần | Giá trị |
|---|---|
| API | https://13-214-87-165.sslip.io (đổi khi có domain) |
| EC2 | t4g.small, Elastic IP 13.214.87.165, Ubuntu 26.04 arm64 |
| DB | RDS Postgres 18, mật khẩu trong Secrets Manager |
| Files | S3 riverbank-data-424016506356 |
| Keys | Secrets Manager riverbank/openai, riverbank/anthropic, riverbank/openrouter |
| Alerts | CloudWatch Logs và alarm qua SNS riverbank-alerts |
| Xem DB | https://13-214-87-165.sslip.io/db (pgweb readonly, basic auth, hỏi admin tài khoản) |
| Xem Valkey | https://13-214-87-165.sslip.io/redis (Redis Commander chỉ đọc, Basic Auth như `/db`, profile `events`) |
| Data train | S3 `exports/<ngày>/*.csv`, sinh bằng `make export` |

EC2 đọc secret bằng IAM role, không có access key trong repo hay trên máy. Local dùng `.env`, không bao giờ commit.
Vận hành, rollback, thêm secret: `.agents/skills/deploy-ops/SKILL.md`.

Migration `007_optional_requested_amount` cho phép tạo hồ sơ khi không nhập số tiền xin vay,
khớp contract `Plan`; cột vẫn được sinh từ kế hoạch, số dương đã nhập vẫn được kiểm tra.
Migration chạy qua `db.migrate`, không sửa checksum migration cũ hoặc thay giá trị hồ sơ.
CI dùng PostgreSQL local riêng `riverbank_test` để kiểm persistence và nâng cấp schema.
Không đặt lại `NOT NULL` khi rollback nếu đã có hồ sơ không nhập số tiền; rollback image
không đồng nghĩa rollback dữ liệu.

Alarm `riverbank-5xx` đếm một access log Caddy cho mỗi request 5xx, kể cả 502 khi API
chưa sẵn sàng; không cộng trùng log middleware hoặc error log proxy. Cấu hình filter
nằm trong `infra/cloudwatch-5xx.json`; cập nhật bằng `aws logs put-metric-filter
--cli-input-json file://infra/cloudwatch-5xx.json --region ap-southeast-1` với profile
đúng tài khoản. Filter mới chỉ áp dụng cho log mới, không sửa metric lịch sử.

## Nguyên tắc code

- Code tính, LLM viết. Mọi con số đi qua `CashFlowEngine` và `PolicyEngine`. LLM chỉ diễn giải.
- Mọi câu trong memo có `evidence_id`.
- Output model qua JSON Schema validator trước khi dùng.
- Có/không, phân loại, chấm mức: dùng Jev (`api/jev.py`), nhận xác suất rồi đặt ngưỡng trong code.
- AI không tự quyết. Hard rule fail là Escalated. Duyệt số khác phải chạy lại rule.
