import json
from types import SimpleNamespace

import pytest

import config
import llm
from llm import CachedLlmClient, LlmClient, LlmError, LlmRequest, LlmResponse
from prompts import PromptStore, default_prompts
from services.underwriting.memo import (
    FallbackMemoWriter,
    LlmMemoWriter,
    MemoComposer,
    MemoConfig,
    TemplateMemoWriter,
    build_composer,
    default_validator,
)
from services.underwriting.models import DecisionInput, Plan, WorkflowError
from services.underwriting.repository import MemoryCaseRepository
from services.underwriting.risk import DemoRiskModel
from services.underwriting.service import UnderwritingService
from services.underwriting.sources import (
    DemoCicSource,
    MockLosAdapter,
    demo_source,
)
from tests.test_underwriting import NOW, plan


def _vnd(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def faithful(slot: dict) -> str:
    claims = slot["claims"]
    numbers = [_vnd(v) for v in claims.values() if type(v) is int]
    words = [v for v in claims.values() if isinstance(v, str)]
    return f"Theo hồ sơ: {', '.join(numbers + words)}."


class ScriptedClient(LlmClient):
    model = "scripted-model"

    def __init__(self, *scripts):
        self.scripts, self.requests = list(scripts), []

    def complete_json(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        script = self.scripts[min(len(self.requests), len(self.scripts)) - 1]
        if isinstance(script, Exception):
            raise script
        slots = request.user["slots"]
        content = {"sentences": [{"slot_id": s["slot_id"], "text": script(s)} for s in slots]}
        return LlmResponse(content, self.model, {"total_tokens": 1})


def _service(client: LlmClient) -> UnderwritingService:
    writer = FallbackMemoWriter(
        LlmMemoWriter(client, default_prompts().get("memo-writer")), TemplateMemoWriter()
    )
    return UnderwritingService(
        MemoryCaseRepository(),
        demo_source(NOW),
        DemoRiskModel(),
        DemoCicSource(),
        MockLosAdapter(),
        clock=lambda: NOW,
        memo=MemoComposer(writer, default_validator()),
    )


def test_llm_memo_keeps_locked_claims_and_rewrites_text():
    client = ScriptedClient(faithful)
    case = _service(client).create(Plan(**plan()), "llm-ok")
    doc = case.documents[-1]
    assert case.status == "PENDING_REVIEW"
    assert (doc["generator"], doc["attempt"], doc["validated"]) == ("LLM", 1, True)
    assert doc["model"] == "scripted-model"
    assert doc["prompt_version"].startswith("memo-writer@")
    gap = next(s for s in doc["seller_explanation"] if s["template"] == "gap")
    e5 = next(e for e in case.evidence if e["evidence_id"] == gap["evidence_id"])
    assert gap["claims"] == {"gap": e5["payload"]["gap"]}
    assert gap["text"] == f"Theo hồ sơ: {_vnd(e5['payload']['gap'])}."


def test_llm_sees_only_slot_claims_and_seller_slots_skip_risk():
    client = ScriptedClient(faithful)
    _service(client).create(Plan(**plan()), "llm-scope")
    payload = client.requests[0].user
    assert set(payload) == {"status_target", "validator_errors", "slots"}
    assert payload["status_target"] == "PENDING_REVIEW"
    for slot in payload["slots"]:
        assert set(slot) == {"slot_id", "audience", "evidence_code", "reference", "claims"}
        assert "weekly_payouts" not in slot["claims"]
    seller = [s for s in payload["slots"] if s["audience"] == "seller_explanation"]
    assert {s["evidence_code"] for s in seller} <= {"E4", "E5"}
    assert client.requests[0].schema_name == "memo_writer"


@pytest.mark.parametrize(
    "bad,code",
    [
        (lambda s: faithful(s) + " Hoặc 88.000.000 VND.", "MEMO_NUMBER_NOT_IN_EVIDENCE"),
        (lambda s: "Hồ sơ cần xác minh thêm.", "MEMO_CLAIM_MISSING"),
        (lambda s: faithful(s) + " Có dấu hiệu gian lận.", "MEMO_FORBIDDEN_LEXICON"),
        (lambda s: faithful(s) + " Hồ sơ đã được duyệt.", "MEMO_CONTRADICTS_STATUS"),
    ],
)
def test_rejected_draft_is_retried_with_validator_feedback(bad, code):
    client = ScriptedClient(bad, faithful)
    case = _service(client).create(Plan(**plan()), "llm-retry")
    first, second = case.documents
    assert not first["validated"] and first["unfinished"]
    assert any(e.endswith(code) for e in first["validation_errors"])
    assert (second["generator"], second["attempt"], second["validated"]) == ("LLM", 2, True)
    feedback = client.requests[1].user["validator_errors"]
    assert feedback and all(code in item["errors"] for item in feedback)
    assert case.status == "PENDING_REVIEW"


def test_last_attempt_falls_back_to_template():
    client = ScriptedClient(lambda s: "Đề xuất 1 đồng.")
    case = _service(client).create(Plan(**plan()), "llm-bad")
    assert [d["generator"] for d in case.documents] == ["LLM", "LLM", "TEMPLATE"]
    assert len(client.requests) == MemoConfig().max_attempts - 1
    assert case.documents[-1]["validated"] and case.status == "PENDING_REVIEW"


@pytest.mark.parametrize(
    "failure",
    [LlmError("LLM_UNAVAILABLE"), LlmError("LLM_INVALID_JSON")],
)
def test_llm_failure_uses_template_in_same_attempt(failure):
    case = _service(ScriptedClient(failure)).create(Plan(**plan()), "llm-down")
    (doc,) = case.documents
    assert doc["generator"] == "TEMPLATE" and doc["validated"]
    assert doc["validation_errors"] == [failure.code]


def test_slot_mismatch_is_a_protocol_error():
    class Partial(ScriptedClient):
        def complete_json(self, request):
            response = super().complete_json(request)
            return LlmResponse({"sentences": response.content["sentences"][1:]}, self.model)

    case = _service(Partial(faithful)).create(Plan(**plan()), "llm-partial")
    assert case.documents[0]["generator"] == "TEMPLATE"
    assert case.documents[0]["validation_errors"] == ["LLM_SLOT_MISMATCH"]


def test_no_loan_llm_memo_cannot_claim_a_loan():
    client = ScriptedClient(lambda s: faithful(s) + " Shop cần vay thêm.", faithful)
    case = _service(client).create(Plan(**plan("demo-a", cash_on_hand=200_000_000)), "llm-no-loan")
    assert case.status == "NO_LOAN_NEEDED"
    assert any("MEMO_CONTRADICTS_STATUS" in e for e in case.documents[0]["validation_errors"])
    assert case.documents[-1]["validated"]


def test_decision_revalidates_llm_memo():
    service = _service(ScriptedClient(faithful))
    case = service.create(Plan(**plan()), "llm-decide")
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="ok")
    assert service.decide(case.case_id, request, "uw", "d1").status == "ACCEPTED"


def test_tampered_llm_text_blocks_decision():
    service = _service(ScriptedClient(faithful))
    case = service.create(Plan(**plan()), "llm-tamper")
    stored = service.repository._cases[case.case_id]
    stored.documents[-1]["underwriter_memo"][1]["text"] += " Thêm 5.000.000 VND."
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="ok")
    with pytest.raises(WorkflowError, match="MEMO_NUMBER_NOT_IN_EVIDENCE"):
        service.decide(case.case_id, request, "uw", "d1")


def test_cached_client_replays_without_calling_model(tmp_path):
    inner = ScriptedClient(faithful)
    cached = CachedLlmClient(inner, tmp_path)
    request = LlmRequest("system", {"slots": [{"slot_id": "a", "claims": {"x": 1}}]}, {}, "t")
    first, second = cached.complete_json(request), cached.complete_json(request)
    assert len(inner.requests) == 1
    assert second.cached and second.content == first.content


def test_openai_client_maps_errors_and_parses_json(monkeypatch):
    from openai import APIConnectionError

    client = llm.OpenAICompatibleClient("sk-test", "m")
    reply = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"sentences": []})))],
        model="m-2026",
        usage=None,
    )
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return reply

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    response = client.complete_json(LlmRequest("s", {"a": 1}, {"type": "object"}, "n", None))
    assert response.content == {"sentences": []} and response.model == "m-2026"
    assert "temperature" not in calls[0]
    assert calls[0]["response_format"]["json_schema"]["strict"] is True

    reply.choices[0].message.content = "not json"
    with pytest.raises(LlmError, match="LLM_INVALID_JSON"):
        client.complete_json(LlmRequest("s", {}, {}, "n"))

    def down(**kwargs):
        raise APIConnectionError(request=None)

    client._client.chat.completions.create = down
    with pytest.raises(LlmError, match="LLM_UNAVAILABLE"):
        client.complete_json(LlmRequest("s", {}, {}, "n"))


def test_build_llm_client_reads_provider_from_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LLM_PROVIDER", "nope")
    with pytest.raises(LlmError, match="LLM_PROVIDER_UNKNOWN"):
        llm.build_llm_client("memo_writer")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    with pytest.raises(LlmError, match="LLM_KEY_MISSING"):
        llm.build_llm_client("memo_writer")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(config, "LLM_CACHE_DIR", str(tmp_path))
    client = llm.build_llm_client("memo_writer")
    assert isinstance(client, CachedLlmClient)
    assert str(client.inner._client.base_url).startswith(config.OPENROUTER_BASE_URL)


def test_build_composer_rejects_unknown_writer():
    assert isinstance(build_composer("template").writer, TemplateMemoWriter)
    with pytest.raises(WorkflowError, match="MEMO_WRITER_UNKNOWN"):
        build_composer("freeform")


def test_prompt_store_reads_front_matter_and_versions(tmp_path):
    prompt = default_prompts().get("memo-writer")
    assert prompt.temperature == 0 and "slot_id" in prompt.system
    (tmp_path / "x.md").write_text("---\nid: y\n---\nbody", encoding="utf-8")
    with pytest.raises(ValueError):
        PromptStore(tmp_path).get("x")
