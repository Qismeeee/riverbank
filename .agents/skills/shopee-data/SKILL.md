---
name: shopee-data
description: Lấy dữ liệu seller Shopee vào Riverbank qua ba nguồn (file xuất Kênh Người Bán, OpenAPI v2, synthetic) và gộp thành chuỗi tuần cho CashFlow Engine. Dùng khi đụng E1 đến E4, FR-05 đến FR-07, parser file seller, client OpenAPI, ETL data.addlivetag.com, schema Postgres, hoặc bộ sinh data demo.
---

# Dữ liệu Shopee

Code: `api/services/shopee/`. Một interface `ShopeeSource.fetch(time_from, time_to) -> ShopData(orders, escrows, returns)` với hai bản `SellerExportSource` (file Kênh Người Bán, parser `SellerExportParser`) và `OpenApiSource` (`ShopeeClient` + `ShopToken`). `WeeklyAggregator(weeks, as_of).build(shop_id, data)` ra `WeeklySeries` rồi vào `CashFlowInput.payout_weekly_history`. Engine không biết nguồn. Nguồn mới: kế thừa `ShopeeSource`. Cột file khác: kế thừa `SellerExportParser`, ghi đè `ALIASES`. Sinh data: `data/synth/generate.py`.

## Sự thật cần biết trước khi hứa gì với ai

- Shopee OpenAPI cần tài khoản developer được duyệt: VN chỉ nhận Preferred hoặc Mall Seller, ISV cần doanh nghiệp có sản phẩm live. Sandbox cũng cần duyệt và không có Payment API. Đã kiểm trên open.shopee.com ngày 21/10/2026.
- Vì vậy `openapi_client.py` viết theo spec và test bằng mock. Không nói "đã chạy live". Khi có partner_id live: tạo secret `riverbank/shopee` {PARTNER_ID, PARTNER_KEY}, seller mở `auth_url()`, redirect về `/shopee/callback`, `exchange_code()`.
- File xuất từ Kênh Người Bán: shop nào cũng xuất được, không cần duyệt. Đây là nguồn dữ liệu thật khả thi nhất. `SellerExportParser.ALIASES` map cột, thiếu cột thì `ColumnError` kèm danh sách cột, thêm alias chứ không đoán. Chưa đối chiếu với file thật, file đầu tiên nhận được thì ẩn danh và bỏ vào `api/tests/fixtures/seller_export/`.
- Synthetic: `python data/synth/generate.py` ra 50 shop, 26 tuần, seed 42. Shop 0 và 1 tái hiện đúng case A và B (test khóa). Phí sàn trong `FEES` là gần đúng.

## ETL dữ liệu công khai (`api/etl/`)

- Kiến trúc: `RawStore` (Local, S3, Mirrored) giữ raw; `Extractor.extract(store, run_date) -> Manifest`; `Transformer.transform(RawRun, date) -> Snapshot(shops, products, report)`; `Loader.load(manifest, snapshot)`; `Pipeline` nối ba bước; `run.py` là CLI mỏng. Bản hiện có: `AddlivetagClient`, `AddlivetagExtractor`, `AddlivetagTransformer`, `PostgresLoader`, `Segmenter` (brand/sme).
- Load thay thế toàn bộ run theo `run_id` trong một transaction, chạy lại cùng ngày không để sót dòng cũ.
- Schema `api/db/schema.sql`, view `v_shop_features` cho Data Analyst. Một DB Postgres, không DuckDB.
- Thêm nguồn mới: kế thừa `Extractor` và `Transformer`, ghi raw qua `RawStore` cùng bố cục, trả `ProductRow`/`ShopRow`. Không ghi thẳng vào Postgres từ extract. Kho khác Postgres: kế thừa `Loader`.
- Đổi quy tắc clean: sửa `transform.py` và `tests/fixtures/market_ao_mua.json` + test cùng PR.
- Test DB: `make db-up` rồi `DATABASE_URL=... pytest tests/test_etl.py`, CI bỏ qua test này.

## Quy ước

- Payout tính theo `escrow_release_time` (tiền về ví), không theo ngày đặt. GMV theo `create_time`, chỉ đơn đã thanh toán.
- Hoàn hàng đếm theo `create_time` của yêu cầu, trạng thái trong `RETURN_COUNTED`.
- Tuần bắt đầu thứ hai, UTC. 12 tuần cho engine, 26 tuần cho feature.
- Không log PII (tên người mua, địa chỉ). `buyer` chỉ giữ dạng mã.
- Raw file lưu S3 `raw/shopee/{shop_id}/{ngày}/`, không commit. `data/synth/out/` trong .gitignore.
