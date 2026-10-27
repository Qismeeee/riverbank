import pytest
from fastapi.testclient import TestClient

import config
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def local_only(monkeypatch):
    # Ép local để test không chạm AWS hay DB dù laptop có .env cloud; chỉ trong file này
    monkeypatch.setattr(config, "ENV", "local")
    monkeypatch.setattr(config, "S3_BUCKET", "")
    monkeypatch.setenv("DATABASE_URL", "")


def test_health_local_skips_external_checks():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "skipped"
    assert body["s3"] == "skipped"


def test_root():
    assert client.get("/").json()["service"] == "riverbank-api"
