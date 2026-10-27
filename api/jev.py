import base64
from pathlib import Path
from typing import Any

import httpx

import config
from services.modelgate import model_gate

URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.14"


def image_part(path: str | Path) -> dict:
    data = base64.b64encode(Path(path).read_bytes()).decode()
    suffix = Path(path).suffix.lstrip(".").lower() or "png"
    return {"type": "image_url", "image_url": {"url": f"data:image/{suffix};base64,{data}"}}


def noul(instructions: str, yes: str, no: str) -> dict:
    return {"type": "noul", "instructions": instructions, "criteria": {"true": yes, "false": no}}


def choice(instructions: str, options: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: str, levels: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": levels}


def decide(
    state: list[Any], questions: dict[str, dict], timeout: float = 30.0, retries: int = 2
) -> dict:
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY chưa cấu hình (.env local hoặc Secrets Manager)")

    def post() -> dict:
        transport = httpx.HTTPTransport(retries=retries)
        with httpx.Client(transport=transport, timeout=timeout) as client:
            r = client.post(
                URL,
                headers={
                    "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                    "HTTP-Referer": "https://github.com/Qismeeee/riverbank",
                    "X-OpenRouter-Title": "Riverbank",
                },
                json={"model": MODEL, "state": state, "questions": questions},
            )
        r.raise_for_status()
        return r.json()["answers"]

    return model_gate().call("jev", post, transient)


def transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


def noul_probability(answers: dict, name: str) -> float:
    value = answers[name]["noul"]
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError(f"Jev trả xác suất không hợp lệ cho {name}")
    return float(value)


if __name__ == "__main__":
    answers = decide(
        state=["Sao kê 6 tháng, doanh thu tăng đều, 2 kỳ bị trừ phí trễ hạn nhỏ."],
        questions={
            "eligible_for_cic": noul(
                "Hồ sơ này có đủ điều kiện để tra CIC chưa?",
                "Dòng tiền đủ, không có dấu hiệu loại trừ",
                "Chưa đủ hoặc có dấu hiệu loại trừ",
            ),
            "risk": score("Mức rủi ro dòng tiền?", ["Thấp", "Vừa", "Cao"]),
        },
    )
    print(answers)
