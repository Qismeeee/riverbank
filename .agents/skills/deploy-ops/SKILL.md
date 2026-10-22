---
name: deploy-ops
description: Deploy, đọc CI/CD trên GitHub, xem log trên EC2 hoặc CloudWatch, rollback, thêm secret, thêm biến môi trường. Dùng khi deploy đỏ, /health lỗi, cần key mới, hoặc cần đổi domain.
---

# Deploy và vận hành

Chi tiết hạ tầng: `docs/Riverbank-TDD.md` mục 9b và `README.md`.

## Luồng

PR → `ci.yml` (ruff, pytest) → merge `main` → `deploy.yml`: test → build arm64 push `ghcr.io/qismeeee/riverbank-api:<sha7>` và `latest` → ssh EC2 → `docker compose pull && up -d` → curl `/health` 12 lần.

Đọc: GitHub → Actions → chọn run → job đỏ → mở step đỏ. `test` đỏ là code, `build` đỏ là Dockerfile hoặc deps, `deploy` đỏ là EC2 hoặc secret.

## Trên EC2

```
ssh -i ~/.ssh/riverbank-key.pem ubuntu@13.214.87.165
cd /opt/riverbank
docker compose ps
docker compose logs -f api --tail 200
docker compose exec api python -c "import config; print(config.ENV)"
```
Log cũng ở CloudWatch log group `/riverbank/app`.

## Giao diện xem DB cho team

`https://<SITE_ADDRESS>/db` là pgweb chạy readonly trên EC2, sau Caddy basic auth, nối RDS bằng role `riverbank_ro` (chỉ SELECT). Cấu hình nằm ở `/opt/riverbank/.env.local` trên EC2 (mẫu `infra/.env.local.example`), deploy không ghi đè file này. Lần đầu:

```
ssh -i ~/.ssh/riverbank-key.pem ubuntu@13.214.87.165
cd /opt/riverbank
docker compose exec api psql "$(docker compose exec api python -c 'import config;print(config.database_url())')" -v ro_password="'MAT_KHAU_RO'" -f /app/db/readonly_role.sql
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'MAT_KHAU_UI'
nano .env.local      # điền DB_UI_USER, DB_UI_PASS_HASH, PGWEB_DATABASE_URL theo mẫu
docker compose up -d
```

Mật khẩu UI và mật khẩu role gửi riêng cho từng người, không dán vào chat nhóm.

## Xuất data để train model

`python -m etl.export --run-date YYYY-MM-DD` chạy trên EC2 (`docker compose exec api ...`) hoặc local: ghi CSV từng bảng (`v_shop_features`, `shop_snapshot`, `product_snapshot`, `etl_run`) vào `data/exports/<ngày>/` và S3 `exports/<ngày>/`. Dev tải về bằng `aws s3 sync s3://riverbank-data-424016506356/exports/<ngày>/ ./data/exports/<ngày>/ --profile riverbank`.

## Rollback

Trên EC2: sửa `IMAGE_TAG=<sha7 cũ>` trong `/opt/riverbank/.env` rồi `docker compose up -d api`. Tag có trong GitHub → Packages.

## Thêm biến môi trường cho api

Không phải secret: thêm vào `infra/docker-compose.yml` phần `environment` của `api`, giá trị trong `infra/.env.prod`. Local thêm vào `.env.example` và `.env`.
Secret: tạo trong Secrets Manager tên `riverbank/<ten>` (region ap-southeast-1), thêm hàm đọc trong `api/config.py` theo mẫu `OPENROUTER_API_KEY`. IAM role đã cho phép mọi `riverbank/*`.

## Domain

Có domain thì trỏ A record về 13.214.87.165, đổi `SITE_ADDRESS` trong `infra/.env.prod`, đổi `ALLOWED_ORIGINS` nếu FE đổi, merge main. Caddy tự lấy cert.

## Không làm

Không `docker compose up` thứ chưa qua PR. Không sửa file trên EC2 bằng tay trừ rollback. Không dán key vào chat hay issue. Không mở port mới trong Security Group mà không hỏi admin AWS.
