# Riverbank: bối cảnh dự án cho Claude Project

Cập nhật 22/10/2026. Đọc file này trước, rồi đọc AGENTS.md, BRD, SRS, TDD, Riverbank-data.md, Riverbank-risk-model.md.

## Dự án

Sea x OpenAI Codex Hackathon Vietnam, thi ngày 31/10/2026 tại Shopee HCM. Team Riverbank 4 người, Le Anh Quan là admin, DevOps và tích hợp. Sản phẩm: cho vay vốn lưu động cho seller Shopee dựa trên dòng tiền payout tuần. Lõi là CashFlow Engine tính gap, Policy Engine chạy rule, risk model PD + SHAP, memo có bằng chứng, người duyệt quyết định. Mô hình tích hợp A: Riverbank là LOS, ngân hàng duyệt trên Lender Portal.

## Cách làm việc

Tiếng Việt, ngắn, thẳng, không vòng vo. Không dùng dấu gạch ngang hay chấm phẩy trong văn bản. Nói thật kể cả tin xấu, số liệu phải kiểm chứng. Hackathon nên không strict quá, nhưng không bịa số và không hứa thứ chưa chạy. Mọi lệnh phân biệt rõ ba nơi: WSL (`loq@Qismeeee`, repo `/mnt/e/riverbank`, git chạy ở đây), EC2 (`ubuntu@ip-172-31-44-176`, chỉ docker compose), AWS console (user quanla6). Key, mật khẩu không dán vào chat.

## Số vàng, test khóa

Case B: gap 80 triệu, đề xuất 90 triệu, 8 tuần, 11,25 triệu/tuần, payout xấu 49 triệu. Case A: gap 0, cảnh báo payout dưới 20 triệu/tuần.

## Repo

`github.com/Qismeeee/riverbank`, public, 3 collaborator. Nhánh `main` bảo vệ bởi ruleset protect-main, PR bắt buộc, CI (ruff + pytest) phải xanh, GitGuardian cảnh báo nhưng không chặn. Merge main là deploy thật.

Cấu trúc: `api/` FastAPI (config đọc .env local hoặc Secrets Manager prod), `api/services/cashflow.py` CashFlowEngine, `api/policy/` PolicyEngine + 9 rule R01 đến R09, `api/services/shopee/` ShopeeSource với SellerExportSource và OpenApiSource, `api/etl/` RawStore, Extractor, Transformer, Loader, Pipeline (bản addlivetag + PostgresLoader), `api/db/schema.sql`, `data/synth/generate.py` (shop demo), `data/synth/train.py` (data huấn luyện có nhãn), `data/synth/calibration.json` (60 shop thật), `eval/golden/`, `.agents/skills/` 7 skill cho Codex và Claude Code, `docs/`. 48 test, chạy không cần mạng. Code không docstring đầu file, mở rộng bằng kế thừa lớp cơ sở.

## Hạ tầng AWS (ap-southeast-1, account 424016506356)

EC2 `riverbank-app` t4g.small, Elastic IP 13.214.87.165, Docker Compose chạy caddy (HTTPS tự động, `https://13-214-87-165.sslip.io`), api (image GHCR tag theo SHA), pgweb. RDS Postgres 18 `riverbank-db` private, chỉ EC2 chạm được. S3 `riverbank-data-424016506356` versioning. Secrets Manager: riverbank/openai, riverbank/anthropic, riverbank/openrouter, rds master. IAM role EC2 least privilege, user riverbank-dev cho laptop. SNS + 3 CloudWatch alarm. Chi phí khoảng 22 USD cho 15 ngày. CI/CD: ci.yml mỗi PR, deploy.yml merge main → build arm64 → ssh EC2 → compose up → curl /health.

Giao diện DB cho team: `https://13-214-87-165.sslip.io/db` (pgweb, basic auth, role riverbank_dev đọc ghi, cấu hình trong /opt/riverbank/.env.local trên EC2, deploy không ghi đè). Mật khẩu UI hiện tại rất yếu, phải đổi.

## Dữ liệu, đã chốt

Shopee OpenAPI không dùng được trước hackathon: cần tài khoản ISV hoặc Preferred/Mall Seller, sandbox cũng cần duyệt và không có Payment API (kiểm trên open.shopee.com ngày 21/10). Crawl shopee.vn không ra payout và vi phạm ToS. File xuất Kênh Người Bán là nguồn seller thật duy nhất, parser đã viết theo bài hướng dẫn chính thức, chưa đối chiếu file thật.

Nguồn thật đang dùng: data.addlivetag.com (dữ liệu công khai đã crawl, API có key, free cho học tập). ETL ngày 22/10: 20 từ khóa, 60 shop (41 sme, 19 brand), 7.071 sản phẩm, raw trên S3 `raw/addlivetag/2026-10-22/`, bảng `shop_snapshot`, `product_snapshot`, view `v_shop_features` trên RDS và Postgres local, CSV ở S3 `exports/2026-10-22/`. Chụp lần 2 ngày 27/10 để có delta lượt bán. Nguồn tự ghi: không phải toàn sàn, sold30d chỉ tin khi có 8 ngày lịch sử.

Risk model: không có nhãn vỡ nợ thật. Train trên synthetic `data/synth/train.py` hiệu chỉnh từ 60 shop thật, nhãn theo BL-11 (3 kỳ trích thiếu liên tiếp). Tập train 20.000 (seed 7), test 5.000 (seed 11) kèm `oracle.csv` (PD thật, trần AUC 0,81). Feature ở `api/services/features.py` (FeatureBuilder, 25 cột, dùng chung train và serving). Kết quả test: logistic AUC 0,774, LightGBM 0,772, Brier 0,128 so với sàn 0,122, calibrator không bật. Notebook đầy đủ `eval/train_pd.ipynb`, headless `eval/train_pd.py`, thư viện `eval/pdlib.py`. Slide ghi rõ PD là minh họa và nói rõ logistic tương đương LightGBM.

Jev (typesafe/jev-1.14 qua OpenRouter): dùng cho gate CIC, gate Document Verifier, chấm chéo memo. Response: noul trả `{"noul": p}`, score trả `score`, `probabilities` theo chỉ số kèm `legend` và `confidence`.

## Việc còn lại

1. Đổi mật khẩu /db, gửi link cho 3 dev, giao việc: Dev 1 routers + schema cases/decisions/jobs; Dev 2 agents (orchestrator, data_analyst, memo_writer, doc_verifier); Dev 3 web Next.js trên Vercel; Quan risk model, eval harness, deploy, demo.
2. Mốc: 24/10 luồng end to end chạy trên synthetic; 27/10 5 case A đến E demo được, chụp ETL lần 2; 29/10 đóng băng tính năng; 31/10 thi.
3. Việc riêng của admin: rotate key riverbank-dev và OpenRouter đã lộ, đổi key addlivetag sau hackathon, root bật Billing và Budget 30/60 USD, MFA, dọn AWS sau 03/11.
4. Email BTC hỏi dataset (docs/email-btc.md), chưa gửi.

## Lỗi đã gặp, đừng lặp

Secret tùy chọn đọc cứng lúc start làm api crash (đã sửa, required=False). Ghi đè file cùng tên staging không tới máy, phải kiểm md5. Load ETL phải xóa run cũ trước khi ghi. Phân khúc shop chốt ở transform khi đã có tên đầy đủ. pre-commit sửa file thì phải add và commit lại. Lệnh dán nhiều dòng vào ssh dễ chạy nhầm máy.
