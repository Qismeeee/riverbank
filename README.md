# Riverbank

Cash-flow based lending for Shopee sellers. Sea x OpenAI Codex Hackathon Vietnam 2026.

## Cấu trúc

```
api/        FastAPI backend. Chạy trên EC2 trong docker.
web/        Next.js frontend. Deploy Vercel, gọi API qua HTTPS.
eval/       Eval harness, chạy offline bằng LLM cache.
infra/      docker-compose.yml, Caddyfile, .env.prod cho EC2.
.github/    ci.yml (test mỗi PR), deploy.yml (merge main = deploy).
```

## Chạy local

Cần Python 3.12 (giống CI và Docker). Dùng `uv` để không phụ thuộc Python hệ thống:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env   # một lần
cp .env.example .env             # điền key, hoặc RIVERBANK_ENV=cloud + AWS_PROFILE để đọc từ Secrets Manager
docker run -d --name rb-pg -e POSTGRES_USER=riverbank -e POSTGRES_PASSWORD=riverbank \
  -e POSTGRES_DB=riverbank -p 5432:5432 postgres:18-alpine                     # DB local
cd api
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r requirements-dev.txt
uvicorn main:app --reload        # http://localhost:8000/docs
pytest -q && ruff check .
python jev.py                    # thử Jev (cần OPENROUTER_API_KEY)
```

## Quy trình

1. Tạo nhánh từ `main`: `git checkout -b feat/ten-tinh-nang`.
2. Push, mở PR. CI chạy ruff + pytest.
3. Merge vào `main` (không cần approval, nhưng CI phải xanh). `deploy.yml` tự build image arm64, push GHCR, ssh vào EC2, `docker compose up`, rồi gọi `/health`.
4. Không push thẳng `main`, không force push. Ruleset chặn.

## Hạ tầng (ap-southeast-1)

| Thành phần | Giá trị |
|---|---|
| API | https://13-214-87-165.sslip.io (đổi khi có domain) |
| EC2 | t4g.small, Elastic IP 13.214.87.165, Ubuntu 26.04 arm64 |
| DB | RDS Postgres 18, mật khẩu trong Secrets Manager |
| Files | S3 riverbank-data-424016506356 |
| Keys | Secrets Manager riverbank/openai, riverbank/anthropic, riverbank/openrouter |
| Alerts | SNS riverbank-alerts, 3 CloudWatch alarm |

EC2 đọc secret bằng IAM role, không có access key trong repo hay trên máy. Local dùng `.env`, không bao giờ commit.

## Nguyên tắc code

- Code tính, LLM viết. Mọi con số đi qua engine Python, LLM chỉ diễn giải.
- Mọi claim trong memo có `evidence_id`.
- Output model phải qua JSON Schema validator trước khi dùng.
- Quyết định có/không, phân loại, chấm mức: dùng Jev (`api/jev.py`), nhận xác suất rồi đặt ngưỡng trong code.
