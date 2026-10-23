import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config


class LlmError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LlmRequest:
    system: str
    user: dict
    schema: dict
    schema_name: str
    temperature: float | None = 0

    def fingerprint(self, model: str) -> str:
        encoded = json.dumps(
            {"model": model, **asdict(self)}, sort_keys=True, ensure_ascii=False
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class LlmResponse:
    content: dict
    model: str
    usage: dict = field(default_factory=dict)
    cached: bool = False


class LlmClient(ABC):
    model: str

    @abstractmethod
    def complete_json(self, request: LlmRequest) -> LlmResponse: ...


class OpenAICompatibleClient(LlmClient):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        if not api_key:
            raise LlmError("LLM_KEY_MISSING")
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries
        )

    def complete_json(self, request: LlmRequest) -> LlmResponse:
        from openai import OpenAIError

        options = {} if request.temperature is None else {"temperature": request.temperature}
        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": json.dumps(request.user, ensure_ascii=False)},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.schema_name,
                        "strict": True,
                        "schema": request.schema,
                    },
                },
                **options,
            )
        except OpenAIError as exc:
            raise LlmError("LLM_UNAVAILABLE") from exc
        try:
            content = json.loads(completion.choices[0].message.content or "")
        except (IndexError, ValueError) as exc:
            raise LlmError("LLM_INVALID_JSON") from exc
        if not isinstance(content, dict):
            raise LlmError("LLM_INVALID_JSON")
        usage = completion.usage.model_dump() if completion.usage else {}
        return LlmResponse(content, completion.model or self.model, usage)


class CachedLlmClient(LlmClient):
    def __init__(self, inner: LlmClient, directory: Path):
        self.inner, self.model, self.directory = inner, inner.model, Path(directory)

    def complete_json(self, request: LlmRequest) -> LlmResponse:
        path = self.directory / f"{request.fingerprint(self.model)}.json"
        if path.exists():
            return LlmResponse(**{**json.loads(path.read_text(encoding="utf-8")), "cached": True})
        response = self.inner.complete_json(request)
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(response), ensure_ascii=False), encoding="utf-8")
        return response


PROVIDERS = {
    "openai": lambda: (config.OPENAI_API_KEY, None),
    "openrouter": lambda: (config.OPENROUTER_API_KEY, config.OPENROUTER_BASE_URL),
}


def build_llm_client(purpose: str) -> LlmClient:
    if config.LLM_PROVIDER not in PROVIDERS:
        raise LlmError("LLM_PROVIDER_UNKNOWN")
    api_key, base_url = PROVIDERS[config.LLM_PROVIDER]()
    client: LlmClient = OpenAICompatibleClient(
        api_key,
        config.LLM_MODEL,
        base_url,
        config.LLM_TIMEOUT_SECONDS,
        config.LLM_MAX_RETRIES,
    )
    if config.LLM_CACHE_DIR:
        client = CachedLlmClient(client, Path(config.LLM_CACHE_DIR) / purpose)
    return client
