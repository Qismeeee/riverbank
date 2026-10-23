import hashlib
import json
import secrets
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from main import app
from routers import underwriting as routes
from scripts import demo_auth
from services.underwriting import auth
from tests.test_underwriting import plan


@pytest.fixture
def production_demo(monkeypatch):
    cfg = routes.config
    for key, value in {
        "ENV": "prod",
        "WORKFLOW_MODE": "demo",
        "WORKFLOW_AUTH": "bearer",
        "WORKFLOW_STORAGE": "memory",
        "WORKFLOW_FEATURES": "demo",
    }.items():
        monkeypatch.setattr(cfg, key, value)
    tokens = {subject: secrets.token_urlsafe(32) for subject in ("demo-a", "demo-b", "uw-test")}
    document = {
        "schema_version": 1,
        "credentials": [
            {
                "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "subject": subject,
                "role": "underwriter" if subject == "uw-test" else "seller",
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            }
            for subject, token in tokens.items()
        ],
    }
    monkeypatch.setattr(cfg, "workflow_demo_credentials", lambda: deepcopy(document))

    def no_database():
        raise AssertionError("production demo must not access the production database")

    monkeypatch.setattr(cfg, "database_url", no_database)
    routes.get_service.cache_clear()
    with TestClient(app) as client:
        yield client, tokens, document
    routes.get_service.cache_clear()


def headers(token):
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": "create-auth-test"}


def test_production_demo_create_review_and_scoped_access(production_demo):
    client, tokens, _ = production_demo
    response = client.post("/v1/cases", json=plan(), headers=headers(tokens["demo-b"]))
    assert response.status_code == 201, response.text
    case_id = response.json()["case_id"]
    assert response.json()["status"] == "PENDING_REVIEW"
    seller = headers(tokens["demo-b"])
    seller.update({"X-Actor-ID": "uw-forged", "X-Actor-Role": "underwriter"})
    assert client.get(f"/v1/cases/{case_id}", headers=seller).status_code == 403
    assert client.get("/v1/queue", headers=seller).status_code == 403
    assert client.get(f"/v1/cases/{case_id}/seller-view", headers=seller).status_code == 200
    other = headers(tokens["demo-a"])
    other["X-Actor-ID"] = "demo-b"
    assert client.post("/v1/cases", json=plan(), headers=other).status_code == 403
    assert client.get(f"/v1/cases/{case_id}/seller-view", headers=other).status_code == 403
    bank = headers(tokens["uw-test"])
    assert client.get(f"/v1/cases/{case_id}", headers=bank).status_code == 200
    assert client.get("/v1/queue", headers=bank).status_code == 200
    assert client.post("/v1/cases", json=plan(), headers=bank).status_code == 403
    decision = client.post(
        f"/v1/cases/{case_id}/decisions",
        headers=bank,
        json={"expected_version": 1, "decision_type": "ACCEPT_PROPOSAL", "reason": "demo"},
    )
    assert decision.status_code == 200 and decision.json()["status"] == "ACCEPTED"


def test_missing_invalid_and_expired_token_cannot_fall_back_to_headers(production_demo):
    client, tokens, document = production_demo
    forged = {"X-Actor-ID": "demo-b", "X-Actor-Role": "seller", "Idempotency-Key": "forged"}
    for authorization in (
        None,
        "Basic invalid",
        "Bearer short",
        "Bearer " + secrets.token_urlsafe(32),
    ):
        supplied = dict(forged)
        if authorization:
            supplied["Authorization"] = authorization
        response = client.post("/v1/cases", json=plan(), headers=supplied)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    for record in document["credentials"]:
        record["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert (
        client.post("/v1/cases", json=plan(), headers=headers(tokens["demo-b"])).status_code == 401
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("WORKFLOW_STORAGE", "postgres"),
        ("WORKFLOW_FEATURES", "postgres"),
        ("WORKFLOW_AUTH", "local_headers"),
        ("WORKFLOW_MODE", "disabled"),
    ],
)
def test_production_demo_rejects_unsafe_configuration(production_demo, monkeypatch, key, value):
    client, tokens, _ = production_demo
    monkeypatch.setattr(routes.config, key, value)
    response = client.post("/v1/cases", json=plan(), headers=headers(tokens["demo-b"]))
    assert response.status_code == 503


def test_secret_errors_fail_closed_without_leaking_credentials(production_demo, monkeypatch):
    client, tokens, document = production_demo
    document["credentials"][0]["role"] = "admin"
    response = client.post("/v1/cases", json=plan(), headers=headers(tokens["demo-b"]))
    assert response.status_code == 503

    def unavailable():
        raise RuntimeError(tokens["demo-b"])

    monkeypatch.setattr(routes.config, "workflow_demo_credentials", unavailable)
    response = client.post("/v1/cases", json=plan(), headers=headers(tokens["demo-b"]))
    assert response.status_code == 503
    assert tokens["demo-b"] not in response.text
    assert response.json()["detail"]["code"] == "DEMO_AUTH_UNAVAILABLE"


def test_token_is_expired_at_exact_deadline(production_demo):
    _, tokens, document = production_demo
    expires = datetime.fromisoformat(document["credentials"][1]["expires_at"])
    with pytest.raises(auth.HTTPException) as caught:
        auth.authenticate_token(tokens["demo-b"], expires)
    assert caught.value.status_code == 401


def test_swagger_exposes_bearer_authorization():
    schema = app.openapi()
    assert schema["components"]["securitySchemes"]["WorkflowDemoBearer"] == {
        "type": "http",
        "description": auth.bearer.model.description,
        "scheme": "bearer",
    }
    assert {"WorkflowDemoBearer": []} in schema["paths"]["/v1/cases"]["post"]["security"]


def test_prod_credentials_ignore_environment_and_refresh_from_aws(monkeypatch):
    cfg = routes.config
    monkeypatch.setattr(cfg, "ENV", "prod")
    monkeypatch.setenv("RIVERBANK_DEMO_AUTH_JSON", '{"schema_version": "untrusted-env"}')
    calls = []
    clock = [120.0]

    class SecretClient:
        def get_secret_value(self, **kwargs):
            calls.append(kwargs)
            return {"SecretString": json.dumps({"version": len(calls)})}

    monkeypatch.setattr(demo_auth.boto3, "client", lambda *a, **k: SecretClient())
    monkeypatch.setattr(cfg.time, "monotonic", lambda: clock[0])
    cfg._workflow_auth_secret.cache_clear()
    try:
        assert cfg.workflow_demo_credentials() == {"version": 1}
        assert cfg.workflow_demo_credentials() == {"version": 1}
        clock[0] = 181.0
        assert cfg.workflow_demo_credentials() == {"version": 2}
        assert calls == [{"SecretId": cfg.WORKFLOW_AUTH_SECRET_ID}] * 2
    finally:
        cfg._workflow_auth_secret.cache_clear()


def test_generate_keeps_tokens_private_and_does_not_overwrite(tmp_path, capsys):
    path = tmp_path / ".env.local"
    demo_auth.generate(path, 7)
    original = path.read_text(encoding="utf-8")
    bundle = json.loads(original)
    output = capsys.readouterr()
    assert len(bundle["tokens"]) == len(bundle["auth"]["credentials"]) == 4
    for token in bundle["tokens"].values():
        assert len(token) >= 32
        assert token not in json.dumps(bundle["auth"])
        assert token not in output.out + output.err
    with pytest.raises(FileExistsError):
        demo_auth.generate(path, 7)
    assert path.read_text(encoding="utf-8") == original


def test_provision_rejects_raw_tokens_and_updates_only_changed_hashes(production_demo, monkeypatch):
    _, tokens, document = production_demo
    state = {}
    writes = []

    class Missing(Exception):
        pass

    class SecretClient:
        class exceptions:
            ResourceNotFoundException = Missing

        def get_secret_value(self, **kwargs):
            if not state:
                raise Missing()
            return state

        def create_secret(self, **kwargs):
            state["SecretString"] = kwargs["SecretString"]
            writes.append("create")

        def put_secret_value(self, **kwargs):
            state["SecretString"] = kwargs["SecretString"]
            writes.append("update")

    monkeypatch.setattr(demo_auth.boto3, "client", lambda *a, **k: SecretClient())
    demo_auth.provision(document)
    demo_auth.provision(document)
    assert writes == ["create"]
    document["credentials"].pop()
    demo_auth.provision(document)
    assert writes == ["create", "update"]
    with pytest.raises(ValueError):
        demo_auth.provision({**document, "tokens": tokens})
    assert all(token not in state["SecretString"] for token in tokens.values())
