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


def test_readiness_returns_503_for_required_dependency_failure(monkeypatch):
    monkeypatch.setattr("main._check_db", lambda: "error: OperationalError")
    monkeypatch.setattr("main._check_s3", lambda: "ok")
    assert client.get("/health").status_code == 200
    response = client.get("/ready")
    assert response.status_code == 503 and response.json()["status"] == "degraded"


def test_readiness_passes_when_checks_are_healthy(monkeypatch):
    monkeypatch.setattr("main._check_db", lambda: "ok")
    monkeypatch.setattr("main._check_s3", lambda: "ok")
    assert client.get("/ready").status_code == 200
