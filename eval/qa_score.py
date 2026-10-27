import json
from pathlib import Path

from services.copilot.answer_guard import normalize

LABELS = Path(__file__).resolve().parent / "golden" / "copilot" / "ask.json"


def labels() -> dict[tuple[str, str], dict]:
    return {
        (row["case_id"], row["question"]): row
        for row in json.loads(LABELS.read_text(encoding="utf-8"))
    }


def errors(label: dict | None, status: str, text: str) -> list[str]:
    if label is None:
        return ["QA_LABEL_MISSING"]
    result = [] if status == label["expected_status"] else ["QA_STATUS_MISMATCH"]
    normalized = normalize(text)
    if any(normalize(phrase) not in normalized for phrase in label["required_phrases"]):
        result.append("QA_REQUIRED_CLAIM_MISSING")
    if label.get("required_actor") == "engine_proposal":
        proposal = any(
            normalize(phrase) in normalized for phrase in ("Hệ thống đề xuất", "Đề xuất", "Số tiền")
        )
        wrong_actor = any(
            normalize(phrase) in normalized
            for phrase in (
                "Seller muốn vay",
                "Shop muốn vay",
                "Mình muốn vay",
                "Tôi muốn vay",
                "Bạn muốn vay",
            )
        )
        if not proposal or wrong_actor:
            result.append("QA_ACTOR_MISMATCH")
    return result
