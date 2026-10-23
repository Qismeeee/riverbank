import hashlib
from dataclasses import dataclass
from pathlib import Path

import config


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    system: str
    temperature: float | None


class PromptStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._loaded: dict[str, Prompt] = {}

    def get(self, prompt_id: str) -> Prompt:
        if prompt_id not in self._loaded:
            self._loaded[prompt_id] = self._load(prompt_id)
        return self._loaded[prompt_id]

    def _load(self, prompt_id: str) -> Prompt:
        text = (self.directory / f"{prompt_id}.md").read_text(encoding="utf-8")
        _, header, body = text.split("---", 2)
        meta = dict(
            (key.strip(), value.strip())
            for key, _, value in (line.partition(":") for line in header.strip().splitlines())
        )
        if meta.get("id") != prompt_id:
            raise ValueError(f"prompt {prompt_id} khai báo id {meta.get('id')}")
        temperature = meta.get("temperature")
        return Prompt(
            id=prompt_id,
            version=f"{prompt_id}@{hashlib.sha256(text.encode()).hexdigest()[:12]}",
            system=body.strip(),
            temperature=float(temperature) if temperature else None,
        )


def default_prompts() -> PromptStore:
    return PromptStore(config.KNOWLEDGE_DIR / "prompts")
