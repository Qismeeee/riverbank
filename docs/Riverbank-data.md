# Nguồn dữ liệu seller Shopee

Bổ sung cho TDD mục 2 và FLOW-01. Kiểm trên open.shopee.com ngày 21/10/2026.

| Nguồn | Cần gì | Có payout không | Dùng trong hackathon |
|---|---|---|---|
| Shopee OpenAPI v2 (live) | Developer account được duyệt: VN chỉ Preferred hoặc Mall Seller, ISV cần doanh nghiệp và sản phẩm live, app review 24 giờ, seller bấm ủy quyền | Có (`payment/get_escrow_detail`) | Không. Client viết theo spec, test mock, sẵn sàng cắm |
| OpenAPI sandbox v2 | Cũng cần account duyệt | Không (chỉ Product, Order, Logistics, Shop) | Không |
| File xuất Kênh Người Bán | Bất kỳ shop nào, seller tự xuất 3 báo cáo | Có (báo cáo doanh thu) | Có, cho seller thật khi có |
| Synthetic (`data/synth`) | Không | Có | Có, mặc định cho demo và test |
| data.addlivetag.com (dữ liệu công khai đã crawl, API có key, free cho học tập) | Tài khoản Google, tạo key | Không. Có doanh thu ước lượng theo shop, `sold30d`, giá, rating | Có, qua `api/etl/`: shop thật, quy mô thật, payout mô phỏng |
| Crawl shopee.vn | Không | Không, vi phạm ToS | Không |

## ETL dữ liệu công khai

`python -m etl.run all --run-date YYYY-MM-DD`. Kiến trúc: `RawStore` → `Extractor` → `Transformer` → `Loader`, nối bằng `Pipeline`, mỗi lớp cơ sở có một bản addlivetag/Postgres. Extract gọi API theo 20 từ khóa (4 ngành), lấy topShops, kéo sản phẩm từng shop, ghi raw JSON nguyên bản lên S3 `raw/addlivetag/{ngày}/` kèm manifest. Transform (code thuần, có test) clean, gộp hai nguồn theo item_id, bỏ quà tặng, tính gmv_30d, aov, top3_share, validate khóa. Load upsert Postgres theo (id, snapshot_date), chạy lại không nhân đôi. Chụp 2 lần (22/10, 27/10) thì view `v_shop_features` có tốc độ bán thật giữa hai mốc. Nguồn tự ghi: dữ liệu crawl, không phải toàn sàn, `sold30d` chỉ tin được khi có 8 ngày lịch sử. Riverbank ghi lại đúng như vậy trong memo.

Thông điệp cho giám khảo: dữ liệu demo synthetic theo đúng schema hai nguồn thật, parser file seller và client OpenAPI có test, seller thật vào bằng file xuất ngay hôm nay và bằng OpenAPI khi Riverbank có tư cách ISV.
