import os

# Ép local trước khi import app, để test không chạm AWS dù laptop có .env cloud
# Đặt chuỗi rỗng thay vì xóa: load_dotenv không ghi đè biến đã có
os.environ["RIVERBANK_ENV"] = "local"
os.environ["S3_BUCKET"] = ""
os.environ["DATABASE_URL"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402

client = TestClient(app)


def test_health_local_skips_external_checks():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "skipped"
    assert body["s3"] == "skipped"


def test_root():
    assert client.get("/").json()["service"] == "riverbank-api"
