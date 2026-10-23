from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from uuid import uuid4

import config
from llm import LlmClient, LlmError, LlmRequest, build_llm_client
from prompts import Prompt, default_prompts
from services.guard import OutputGuard, ints, numbers

from .evidence import TEMPLATES, read_evidence, sentence
from .models import Case, WorkflowError

AUDIENCES = ("underwriter_memo", "seller_explanation")
SHARED_SECTIONS = (("source", "E4"), ("gap", "E5"))
LOAN_SECTIONS = (("proposal", "E5"), ("deduction", "E5"))
NO_LOAN_SECTIONS = (("no_loan", "E5"),)
INTERNAL_SECTIONS = (("risk", "E6"), ("policy", "E7"), ("statement", "E8"))
VARIANTS = {"risk": lambda payload: "risk_missing" if payload.get("missing_fields") else "risk"}
GUARD_CODES = {
    "lexicon": "MEMO_FORBIDDEN_LEXICON",
    "number": "MEMO_NUMBER_NOT_IN_EVIDENCE",
    "missing_evidence": "MEMO_NUMBER_NOT_IN_EVIDENCE",
    "contradicts_status": "MEMO_CONTRADICTS_STATUS",
}


@dataclass(frozen=True)
class MemoConfig:
    max_attempts: int = 3


@dataclass(frozen=True)
class Slot:
    slot_id: str
    audience: str
    template: str
    evidence: dict


@dataclass(frozen=True)
class MemoRequest:
    status: str
    slots: list[Slot]
    feedback: list[dict]
    attempt: int
    max_attempts: int


@dataclass
class Draft:
    sentences: dict[str, list[dict]]
    generator: str
    prompt_version: str
    model: str | None = None
    notes: list[str] = field(default_factory=list)


def variant(template: str, payload: dict) -> str:
    return VARIANTS[template](payload) if template in VARIANTS else template


def plan_slots(run: dict, refs: dict) -> list[Slot]:
    loan = LOAN_SECTIONS if run["cashflow"]["gap"] else NO_LOAN_SECTIONS
    shared = SHARED_SECTIONS + loan
    internal = shared + tuple(
        (variant(t, refs[code]["payload"]), code) for t, code in INTERNAL_SECTIONS if code in refs
    )
    sections = {"underwriter_memo": internal, "seller_explanation": shared}
    return [
        Slot(f"{audience}.{index}", audience, template, refs[code])
        for audience in AUDIENCES
        for index, (template, code) in enumerate(sections[audience])
    ]


def group(slots: list[Slot], render) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {audience: [] for audience in AUDIENCES}
    for slot in slots:
        grouped[slot.audience].append(render(slot))
    return grouped


class MemoWriter(ABC):
    @abstractmethod
    def write(self, request: MemoRequest) -> Draft: ...


class TemplateMemoWriter(MemoWriter):
    version = "templates-v1"

    def write(self, request: MemoRequest) -> Draft:
        rendered = group(request.slots, lambda slot: sentence(slot.template, slot.evidence))
        return Draft(rendered, "TEMPLATE", self.version)


class LlmMemoWriter(MemoWriter):
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["sentences"],
        "properties": {
            "sentences": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["slot_id", "text"],
                    "properties": {"slot_id": {"type": "string"}, "text": {"type": "string"}},
                },
            }
        },
    }

    def __init__(self, client: LlmClient, prompt: Prompt):
        self.client, self.prompt = client, prompt

    def _payload(self, request: MemoRequest) -> dict:
        return {
            "status_target": request.status,
            "validator_errors": request.feedback,
            "slots": [
                {
                    "slot_id": slot.slot_id,
                    "audience": slot.audience,
                    "evidence_code": slot.evidence["code"],
                    "reference": reference["text"],
                    "claims": reference["claims"],
                }
                for slot in request.slots
                for reference in (sentence(slot.template, slot.evidence),)
            ],
        }

    def write(self, request: MemoRequest) -> Draft:
        response = self.client.complete_json(
            LlmRequest(
                system=self.prompt.system,
                user=self._payload(request),
                schema=self.schema,
                schema_name="memo_writer",
                temperature=self.prompt.temperature,
            )
        )
        items = response.content.get("sentences")
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise LlmError("LLM_SCHEMA_MISMATCH")
        texts = {item.get("slot_id"): item.get("text") for item in items}
        expected = {slot.slot_id for slot in request.slots}
        if len(items) != len(texts) or set(texts) != expected:
            raise LlmError("LLM_SLOT_MISMATCH")
        if not all(isinstance(text, str) and text.strip() for text in texts.values()):
            raise LlmError("LLM_SCHEMA_MISMATCH")
        rendered = group(
            request.slots,
            lambda slot: {
                **sentence(slot.template, slot.evidence),
                "text": texts[slot.slot_id].strip(),
            },
        )
        return Draft(rendered, "LLM", self.prompt.version, response.model)


class FallbackMemoWriter(MemoWriter):
    def __init__(self, primary: MemoWriter, fallback: MemoWriter):
        self.primary, self.fallback = primary, fallback

    def write(self, request: MemoRequest) -> Draft:
        if request.attempt >= request.max_attempts:
            return self.fallback.write(request)
        try:
            return self.primary.write(request)
        except LlmError as exc:
            draft = self.fallback.write(request)
            draft.notes.append(exc.code)
            return draft


class MemoValidator:
    def __init__(self, guard: OutputGuard):
        self.guard = guard

    def sentence_errors(self, case: Case, item: dict) -> list[str]:
        try:
            evidence = read_evidence(case, item["evidence_id"])
        except WorkflowError as exc:
            return [exc.code]
        if item.get("template") not in TEMPLATES:
            return ["MEMO_TEMPLATE_UNKNOWN"]
        claims = sentence(item["template"], evidence)["claims"]
        codes = [] if item.get("claims") == claims else ["MEMO_EVIDENCE_MISMATCH"]
        text = item.get("text") or ""
        codes += [GUARD_CODES[r] for r in self.guard.check(text, ints(claims), case.status)]
        required = {v for v in claims.values() if type(v) is int}
        if not required <= set(numbers(text)):
            codes.append("MEMO_CLAIM_MISSING")
        return list(dict.fromkeys(codes))

    def errors(self, case: Case, sentences: dict[str, list[dict]]) -> list[dict]:
        return [
            {"slot_id": f"{audience}.{index}", "text": item.get("text"), "errors": codes}
            for audience in AUDIENCES
            for index, item in enumerate(sentences.get(audience, []))
            for codes in (self.sentence_errors(case, item),)
            if codes
        ]

    def validate(self, case: Case, document: dict) -> None:
        failures = self.errors(case, {a: document[a] for a in AUDIENCES})
        if failures:
            raise WorkflowError(failures[0]["errors"][0], details=failures)


class MemoComposer:
    def __init__(
        self,
        writer: MemoWriter,
        validator: MemoValidator,
        cfg: MemoConfig | None = None,
    ):
        self.writer, self.validator, self.cfg = writer, validator, cfg or MemoConfig()

    def compose(self, case: Case, run: dict, refs: dict, now: str) -> list[dict]:
        slots, feedback, documents = plan_slots(run, refs), [], []
        for attempt in range(1, self.cfg.max_attempts + 1):
            request = MemoRequest(case.status, slots, feedback, attempt, self.cfg.max_attempts)
            draft = self.writer.write(request)
            feedback = self.validator.errors(case, draft.sentences)
            documents.append(
                {
                    "document_id": str(uuid4()),
                    "run_id": run["run_id"],
                    "attempt": attempt,
                    **draft.sentences,
                    "validated": not feedback,
                    "unfinished": bool(feedback),
                    "validation_errors": draft.notes
                    + [f"{f['slot_id']}:{code}" for f in feedback for code in f["errors"]],
                    "generator": draft.generator,
                    "prompt_version": draft.prompt_version,
                    "model": draft.model,
                    "created_at": now,
                }
            )
            if not feedback:
                break
        return documents


def default_validator() -> MemoValidator:
    return MemoValidator(OutputGuard.from_guardrails(config.KNOWLEDGE_DIR / "guardrails.md"))


WRITERS = {
    "template": lambda: TemplateMemoWriter(),
    "llm": lambda: FallbackMemoWriter(
        LlmMemoWriter(build_llm_client("memo_writer"), default_prompts().get("memo-writer")),
        TemplateMemoWriter(),
    ),
}


def build_composer(kind: str) -> MemoComposer:
    if kind not in WRITERS:
        raise WorkflowError("MEMO_WRITER_UNKNOWN", 503)
    return MemoComposer(WRITERS[kind](), default_validator())
