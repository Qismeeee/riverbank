from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from main import app
from routers import auth
from routers import underwriting as routes
from services.underwriting.models import WorkflowError
from services.underwriting.repository import MemoryCaseRepository
from services.underwriting.risk import DemoRiskModel
from services.underwriting.service import UnderwritingService, utc_now
from services.underwriting.sources import DemoCicSource, MockLosAdapter, demo_source
from tests.test_underwriting import NOW, plan

KEY = "k" * 32
PASSCODE = "demo-pass"


@pytest.fixture
def token_mode(monkeypatch, service):
    monkeypatch.setattr(auth.config, "ENV", "prod")
    monkeypatch.setattr(auth.config, "WORKFLOW_MODE", "demo")
    monkeypatch.setattr(auth.config, "WORKFLOW_IDENTITY", "token")
    monkeypatch.setattr(auth.config, "AUTH_SIGNING_KEY", KEY)
    monkeypatch.setattr(auth.config, "DEMO_PASSCODE", PASSCODE)
    monkeypatch.setattr(auth, "throttle", auth.LoginThrottle(3, 60))
    app.dependency_overrides[routes.get_service] = lambda: service
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def service():
    return UnderwritingService(
        MemoryCaseRepository(),
        demo_source(NOW),
        DemoRiskModel(),
        DemoCicSource(),
        MockLosAdapter(),
        clock=lambda: NOW,
    )


def login(client, subject, role, passcode=PASSCODE):
    return client.post(
        "/v1/auth/token", json={"subject": subject, "role": role, "passcode": passcode}
    )


def bearer(client, subject, role, **extra):
    token = login(client, subject, role).json()["access_token"]
    return {"Authorization": f"Bearer {token}", **extra}


def code(response):
    return response.status_code, response.json()["detail"]["code"]


def test_token_login_drives_seller_and_underwriter_flow(token_mode):
    response = login(token_mode, "demo-b", "seller")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer" and body["subject"] == "demo-b"
    seller = bearer(token_mode, "demo-b", "seller", **{"Idempotency-Key": "k1"})
    assert token_mode.get("/v1/auth/me", headers=seller).json() == {
        "subject": "demo-b",
        "role": "seller",
    }
    created = token_mode.post("/v1/cases", json=plan(), headers=seller)
    assert created.status_code == 201, created.text
    bank = bearer(token_mode, "uw-1", "underwriter")
    queue = token_mode.get("/v1/queue", headers=bank).json()["items"]
    assert [c["case_id"] for c in queue] == [created.json()["case_id"]]
    assert code(token_mode.get("/v1/queue", headers=seller)) == (403, "UNDERWRITER_REQUIRED")


def test_seller_token_cannot_act_for_another_seller(token_mode):
    headers = bearer(token_mode, "demo-a", "seller", **{"Idempotency-Key": "k1"})
    response = token_mode.post("/v1/cases", json=plan("demo-b"), headers=headers)
    assert code(response) == (403, "SELLER_SCOPE_MISMATCH")


def test_wrong_passcode_and_spoofed_headers_are_rejected(token_mode):
    assert code(login(token_mode, "uw-1", "underwriter", "nope")) == (401, "INVALID_CREDENTIALS")
    spoof = {"X-Actor-ID": "uw-1", "X-Actor-Role": "underwriter"}
    assert code(token_mode.get("/v1/queue", headers=spoof)) == (401, "AUTHENTICATION_REQUIRED")


def test_tampered_and_expired_tokens_are_rejected(token_mode):
    token = login(token_mode, "demo-b", "seller").json()["access_token"]
    header, payload, signature = token.split(".")
    forged = auth._b64(auth._unb64(payload).replace(b"seller", b"underwriter"))
    for bad in (f"{header}.{forged}.{signature}", "abc", f"{token}x"):
        response = token_mode.get("/v1/auth/me", headers={"Authorization": f"Bearer {bad}"})
        assert code(response) == (401, "INVALID_TOKEN")
    old, _ = auth.signer().issue(auth.Actor("demo-b", "seller"), utc_now() - timedelta(days=1))
    response = token_mode.get("/v1/auth/me", headers={"Authorization": f"Bearer {old}"})
    assert code(response) == (401, "TOKEN_EXPIRED")


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"WORKFLOW_IDENTITY": "header"}, (503, "TOKEN_LOGIN_DISABLED")),
        ({"DEMO_PASSCODE": ""}, (503, "AUTH_NOT_CONFIGURED")),
        ({"AUTH_SIGNING_KEY": "short"}, (503, "AUTH_NOT_CONFIGURED")),
    ],
)
def test_token_login_requires_configuration(token_mode, monkeypatch, changes, expected):
    for name, value in changes.items():
        monkeypatch.setattr(auth.config, name, value)
    assert code(login(token_mode, "demo-b", "seller")) == expected


def test_header_identity_is_local_only(monkeypatch):
    monkeypatch.setattr(auth.config, "WORKFLOW_MODE", "demo")
    monkeypatch.setattr(auth.config, "WORKFLOW_IDENTITY", "header")
    monkeypatch.setattr(auth.config, "ENV", "prod")
    with pytest.raises(WorkflowError) as error:
        auth.get_actor(None, "uw-1", "underwriter")
    assert error.value.code == "VERIFIED_IDENTITY_ADAPTER_REQUIRED"
    monkeypatch.setattr(auth.config, "WORKFLOW_IDENTITY", "unknown")
    with pytest.raises(WorkflowError) as error:
        auth.get_actor(None, "uw-1", "underwriter")
    assert error.value.code == "INVALID_WORKFLOW_IDENTITY"


def test_repeated_wrong_passcodes_are_throttled(token_mode):
    for _ in range(3):
        assert code(login(token_mode, "uw-1", "underwriter", "nope"))[0] == 401
    assert code(login(token_mode, "uw-1", "underwriter")) == (429, "TOO_MANY_ATTEMPTS")


def test_throttle_window_and_reset():
    throttle, now = auth.LoginThrottle(2, 60), utc_now()
    throttle.fail("ip", now)
    throttle.fail("ip", now)
    with pytest.raises(WorkflowError):
        throttle.check("ip", now)
    throttle.check("ip", now + timedelta(seconds=61))
    throttle.fail("ip", now)
    throttle.reset("ip")
    throttle.check("ip", now)


def test_openapi_exposes_bearer_scheme_for_authorize_button(token_mode):
    spec = token_mode.get("/openapi.json").json()
    params = {p["name"].lower() for p in spec["paths"]["/v1/cases"]["post"]["parameters"]}
    assert spec["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert {"HTTPBearer": []} in spec["paths"]["/v1/cases"]["post"]["security"]
    assert "authorization" not in params


def test_lowercase_bearer_scheme_is_accepted(token_mode):
    token = login(token_mode, "uw-1", "underwriter").json()["access_token"]
    response = token_mode.get("/v1/auth/me", headers={"Authorization": f"bearer {token}"})
    assert response.status_code == 200
