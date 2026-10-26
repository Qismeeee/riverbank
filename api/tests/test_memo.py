import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

import config
import llm
from llm import CachedLlmClient, LlmClient, LlmError, LlmRequest, LlmResponse
from prompts import PromptStore, default_prompts
from services.underwriting.memo import (
    CodeMemoWriter,
    LlmMemoWriter,
    MemoComposer,
    TemplateMemoWriter,
    build_composer,
    default_validator,
)
from services.underwriting.models import DecisionInput, Plan, WorkflowError
from services.underwriting.repository import MemoryCaseRepository
from services.underwriting.risk import BaselineRiskModel
from services.underwriting.service import UnderwritingService
from services.underwriting.sources import (
    FixedCicSource,
    InternalLosAdapter,
    seller_book_source,
)
from tests.test_underwriting import NOW, plan


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


def _service(writer) -> UnderwritingService:
    return UnderwritingService(
        MemoryCaseRepository(),
        seller_book_source(NOW),
        BaselineRiskModel(),
        FixedCicSource(),
        InternalLosAdapter(),
        clock=lambda: NOW,
        memo=MemoComposer(writer, default_validator()),
    )


def test_locked_facts_are_code_rendered_without_calling_llm():
    client = ScriptedClient(lambda slot: slot["reference"])
    writer = LlmMemoWriter(client, default_prompts().get("memo-writer"))
    case = _service(writer).create(Plan(**plan()), "memo-code")
    doc = case.documents[-1]
    assert case.status == "PENDING_REVIEW"
    assert (doc["generator"], doc["attempt"], doc["validated"]) == ("CODE", 1, True)
    assert doc["model"] is None
    assert client.requests == []
    gap = next(s for s in doc["seller_explanation"] if s["template"] == "gap")
    e5 = next(e for e in case.evidence if e["evidence_id"] == gap["evidence_id"])
    assert gap["claims"] == {"gap": e5["payload"]["gap"]}
    assert gap["text"] == f"Hệ thống tính khoảng thiếu vốn là {e5['payload']['gap']} VND."
    assert gap["semantic_frame"]["actor"] == "cashflow_engine"
    assert gap["semantic_frame"]["roles"] == gap["claims"]


def test_seller_explanation_has_three_code_owned_fact_sentences():
    case = _service(CodeMemoWriter()).create(Plan(**plan()), "memo-seller")
    seller = case.documents[-1]["seller_explanation"]
    assert [item["template"] for item in seller] == ["gap", "proposal", "deduction"]
    assert all(item["locked"] for item in seller)
    proposal = seller[1]
    assert proposal["semantic_frame"]["relation"] == "proposes_loan_terms"
    assert proposal["semantic_frame"]["units"] == {
        "amount_proposed": "VND",
        "tenor_weeks": "WEEK",
    }
    assert "Hệ thống đề xuất" in proposal["text"]
    assert "muốn vay" not in proposal["text"].lower()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda text: text.replace("90000000 VND trong 8 tuần", "8 VND trong 90000000 tuần"),
        lambda text: text.replace("Hệ thống đề xuất", "Seller muốn vay"),
        lambda text: "Hệ thống đề xuất chín mươi triệu đồng trong tám tuần.",
        lambda text: text + " Phí xử lý là 2 phần trăm.",
        lambda text: text + " Hồ sơ đã chấp thuận.",
    ],
)
def test_semantic_validator_rejects_adversarial_fact_rewrites(mutate):
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "memo-adversarial")
    item = deepcopy(case.documents[-1]["seller_explanation"][1])
    item["text"] = mutate(item["text"])
    errors = default_validator().sentence_errors(case, item, "seller_explanation")
    assert "MEMO_SEMANTIC_MISMATCH" in errors


def test_decision_revalidates_code_memo():
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "memo-decide")
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="ok")
    assert service.decide(case.case_id, request, "uw", "d1").status == "ACCEPTED"


def test_tampered_llm_text_blocks_decision():
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "llm-tamper")
    stored = service.repository._cases[case.case_id]
    stored.documents[-1]["underwriter_memo"][1]["text"] += " Thêm 5.000.000 VND."
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="ok")
    with pytest.raises(WorkflowError, match="MEMO_NUMBER_NOT_IN_EVIDENCE"):
        service.decide(case.case_id, request, "uw", "d1")


def test_missing_locked_slot_blocks_decision():
    service = _service(CodeMemoWriter())
    case = service.create(Plan(**plan()), "memo-missing-slot")
    stored = service.repository._cases[case.case_id]
    stored.documents[-1]["seller_explanation"].pop()
    request = DecisionInput(expected_version=1, decision_type="ACCEPT_PROPOSAL", reason="ok")
    with pytest.raises(WorkflowError, match="MEMO_SLOT_MISMATCH"):
        service.decide(case.case_id, request, "uw", "d1")


def test_cached_client_replays_without_calling_model(tmp_path):
    inner = ScriptedClient(lambda slot: slot.get("reference", "safe"))
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
