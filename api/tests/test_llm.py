import json
from types import SimpleNamespace

import pytest

import config
import llm
from llm import CachedLlmClient, LlmClient, LlmError, LlmRequest, LlmResponse
from prompts import PromptStore, default_prompts


class EchoClient(LlmClient):
    model = "echo-model"

    def __init__(self):
        self.requests = []

    def complete_json(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse({"echo": request.user}, self.model, {"total_tokens": 1})


def test_cached_client_replays_without_calling_model(tmp_path):
    inner = EchoClient()
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
        llm.build_llm_client("copilot_ask")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    with pytest.raises(LlmError, match="LLM_KEY_MISSING"):
        llm.build_llm_client("copilot_ask")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(config, "LLM_CACHE_DIR", str(tmp_path))
    client = llm.build_llm_client("copilot_ask")
    assert isinstance(client, CachedLlmClient)
    assert client.provider == "openrouter" and client.inner.provider == "openrouter"
    assert str(client.inner._client.base_url).startswith(config.OPENROUTER_BASE_URL)


def test_prompt_store_reads_front_matter_and_versions(tmp_path):
    prompt = default_prompts().get("copilot-qa")
    assert prompt.temperature == 0 and prompt.version.startswith("copilot-qa@")
    (tmp_path / "x.md").write_text("---\nid: y\n---\nbody", encoding="utf-8")
    with pytest.raises(ValueError):
        PromptStore(tmp_path).get("x")
