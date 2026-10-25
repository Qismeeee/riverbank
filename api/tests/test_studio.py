import pytest

from policy.rules import RULES
from services.studio import eval_report
from services.underwriting.models import WorkflowError
from tests.test_golden import CASES
from tests.test_underwriting import (  # noqa: F401
    BANK,
    client,
    create,
    plan,
    seller_headers,
    service,
)


def test_policy_snapshot_reflects_running_service(client, service):  # noqa: F811
    body = client.get("/v1/studio/policy", headers=BANK).json()
    assert body["policy_hash"] == service.policy_hash
    assert [r["rule_id"] for r in body["rules"]] == [r.rule_id for r in RULES]
    assert body["policy_config"]["max_amount"] == service.policy.cfg.max_amount
    assert body["cashflow_config"]["stress_factor"] == service.engine.cfg.stress_factor
    assert body["risk_model"]["source"] == "demo"
    assert body["cic_gate"] == {"gate": "cic", "model": "open", "threshold": 0.0}
    assert body["cic_source"] == service.cic.describe()


def test_eval_metrics_cover_every_golden_case(client):  # noqa: F811
    body = client.get("/v1/studio/eval-metrics", headers=BANK).json()
    assert body["summary"]["total"] == len(CASES) == len(body["cases"])
    assert body["summary"]["failed"] == 0


def test_studio_requires_underwriter(client):  # noqa: F811
    response = client.get("/v1/studio/policy", headers=seller_headers())
    assert response.json()["detail"]["code"] == "UNDERWRITER_REQUIRED"


def test_missing_eval_report(tmp_path):
    with pytest.raises(WorkflowError) as error:
        eval_report(tmp_path / "missing.json")
    assert (error.value.code, error.value.status) == ("EVAL_REPORT_MISSING", 503)


def test_seller_lists_only_own_cases(client):  # noqa: F811
    first = create(client)["case_id"]
    second = client.post(
        "/v1/cases",
        json=plan(requested_amount=140_000_000),
        headers=seller_headers(key="create-2"),
    ).json()["case_id"]
    mine = client.get("/v1/me/cases", headers=seller_headers()).json()
    items = mine["items"]
    assert {c["case_id"] for c in items} == {first, second}
    keys = [(c["created_at"], c["case_id"]) for c in items]
    assert keys == sorted(keys, reverse=True)
    assert client.get("/v1/me/cases", headers=seller_headers("demo-a")).json()["items"] == []
    response = client.get("/v1/me/cases", headers=BANK)
    assert response.json()["detail"]["code"] == "SELLER_REQUIRED"
