import re

NUMBER = re.compile(r"\d{1,3}(?:\.\d{3})+|\d+")


def _spans(text: str) -> list[re.Match]:
    return list(NUMBER.finditer(text))


def _swap(text: str) -> str | None:
    spans = _spans(text)
    pair = next(
        ((a, b) for i, a in enumerate(spans) for b in spans[i + 1 :] if a.group() != b.group()),
        None,
    )
    if not pair:
        return None
    a, b = pair
    return text[: a.start()] + b.group() + text[a.end() : b.start()] + a.group() + text[b.end() :]


def _alter(text: str) -> str | None:
    spans = _spans(text)
    if not spans:
        return None
    first = spans[0]
    value = int(first.group().replace(".", "")) + 1
    bumped = f"{value:,}".replace(",", ".") if "." in first.group() else str(value)
    return text[: first.start()] + bumped + text[first.end() :]


def _drop(text: str) -> str | None:
    spans = _spans(text)
    if not spans:
        return None
    first = spans[0]
    return text[: first.start()] + "một khoản" + text[first.end() :]


def _approved(text: str) -> str | None:
    return text.rstrip(".") + ". Hồ sơ đã được duyệt."


def _accuse(text: str) -> str | None:
    return text.rstrip(".") + ". Có dấu hiệu gian lận."


MUTATIONS = {
    "swap_numbers": _swap,
    "alter_number": _alter,
    "drop_number": _drop,
    "claims_approved": _approved,
    "accuses_fraud": _accuse,
}


def mutations(text: str) -> list[tuple[str, str]]:
    return [
        (name, mutated)
        for name, mutate in MUTATIONS.items()
        if (mutated := mutate(text)) is not None and mutated != text
    ]
