# Test tải

`load_test.py` cho nhiều người dùng ảo chạy song song luồng hồ sơ thật của `eval/dossiers/flow_check.py`: tạo hồ sơ, nộp sao kê qua job, người duyệt quyết định, bàn giao LOS. Mỗi người dùng lần lượt chạy các hồ sơ HS01 đến HS06, nghỉ `--think` giây giữa hai hồ sơ.

```bash
cd api
read -s RIVERBANK_PASSCODE; export RIVERBANK_PASSCODE
.venv/bin/python ../eval/load/load_test.py --base https://<host> --dossiers <thư mục hồ sơ> \
  --stages 2:60,5:120,10:120,20:120 --out load-report.json
```

| Tham số | Ý nghĩa |
|---|---|
| `--stages` | Các nấc tải `số người:giây`, chạy lần lượt |
| `--llm` | Thêm bước chat ra phương án và Copilot, gọi model thật, tốn tiền |
| `--think` | Giây nghỉ giữa hai hồ sơ của một người dùng |
| `--only` | Chỉ chạy một số hồ sơ, ví dụ `HS02,HS05` |

Mỗi nấc in số request, RPS, P95, P99, tỉ lệ 5xx, số lần bị 429 và số luồng chạy đúng kết quả mong đợi. File report có thêm số liệu theo từng route (`POST /v1/cases`, `GET /v1/jobs/{id}`...) và mọi mẫu đo.

Mỗi hồ sơ chạy tạo một case thật. `case_events` không cho xoá, nên chạy trên prod là để lại dữ liệu test vĩnh viễn. Giới hạn mặc định mỗi seller là 30 hồ sơ và 20 lần nộp sao kê mỗi giờ (`RIVERBANK_RATE_LIMIT_*`), test lâu sẽ gặp 429.
