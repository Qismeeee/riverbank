from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from uuid import uuid4

import config
from services.guard import OutputGuard, in_order, ints, numbers

from .evidence import TEMPLATES, read_evidence, sentence
from .models import Case, WorkflowError

AUDIENCES = ("underwriter_memo", "seller_explanation")
SHARED_SECTIONS = (("source", "E4"), ("gap", "E5"))
SELLER_SECTIONS = (("gap", "E5"),)
SELLER_NO_LOAN_SECTIONS = (("gap", "E5"), ("no_loan", "E5"), ("source", "E4"))
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
    internal = (
        SHARED_SECTIONS
        + loan
        + tuple(
            (variant(t, refs[code]["payload"]), code)
            for t, code in INTERNAL_SECTIONS
            if code in refs
        )
    )
    seller = SELLER_SECTIONS + loan if run["cashflow"]["gap"] else SELLER_NO_LOAN_SECTIONS
    sections = {"underwriter_memo": internal, "seller_explanation": seller}
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


class CodeMemoWriter(MemoWriter):
    version = "semantic-frames-v1"

    def write(self, request: MemoRequest) -> Draft:
        rendered = group(
            request.slots,
            lambda slot: sentence(slot.template, slot.evidence, slot.audience),
        )
        return Draft(rendered, "CODE", self.version)


def framed(sentences: dict[str, list[dict]]) -> bool:
    return any("semantic_frame" in item for items in sentences.values() for item in items)


class MemoValidator:
    def __init__(self, guard: OutputGuard):
        self.guard = guard

    def sentence_errors(
        self, case: Case, item: dict, audience: str = "underwriter_memo", legacy: bool = False
    ) -> list[str]:
        try:
            evidence = read_evidence(case, item["evidence_id"])
        except WorkflowError as exc:
            return [exc.code]
        if item.get("template") not in TEMPLATES:
            return ["MEMO_TEMPLATE_UNKNOWN"]
        expected = sentence(item["template"], evidence, audience)
        claims = expected["claims"]
        codes = [] if item.get("claims") == claims else ["MEMO_EVIDENCE_MISMATCH"]
        text = item.get("text") or ""
        codes += [GUARD_CODES[r] for r in self.guard.check(text, ints(claims), case.status)]
        if not ints(claims) <= set(numbers(text)):
            codes.append("MEMO_CLAIM_MISSING")
        if not in_order(text, expected["text"]):
            codes.append("MEMO_NUMBER_ORDER")
        if not legacy and (
            item.get("semantic_frame") != expected["semantic_frame"] or text != expected["text"]
        ):
            codes.append("MEMO_SEMANTIC_MISMATCH")
        return list(dict.fromkeys(codes))

    def errors(
        self, case: Case, sentences: dict[str, list[dict]], legacy: bool = False
    ) -> list[dict]:
        failures = [
            {"slot_id": f"{audience}.{index}", "text": item.get("text"), "errors": codes}
            for audience in AUDIENCES
            for index, item in enumerate(sentences.get(audience, []))
            for codes in (self.sentence_errors(case, item, audience, legacy),)
            if codes
        ]
        if legacy:
            return failures
        run = case.runs[-1]
        refs = {item["code"]: item for item in case.evidence if item["run_id"] == run["run_id"]}
        expected = plan_slots(run, refs)
        for audience in AUDIENCES:
            wanted = [slot for slot in expected if slot.audience == audience]
            actual = sentences.get(audience, [])
            if len(actual) != len(wanted) or any(
                item.get("template") != slot.template
                or item.get("evidence_id") != slot.evidence["evidence_id"]
                for item, slot in zip(actual, wanted, strict=False)
            ):
                failures.append(
                    {"slot_id": audience, "text": None, "errors": ["MEMO_SLOT_MISMATCH"]}
                )
        return failures

    def validate(self, case: Case, document: dict) -> None:
        sentences = {a: document[a] for a in AUDIENCES}
        failures = self.errors(case, sentences, legacy=not framed(sentences))
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
