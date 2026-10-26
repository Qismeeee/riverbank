import argparse
import json
import os
import sys
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = (
    "Mỗi tuần tôi bị trừ bao nhiêu tiền và trong bao lâu?",
    "Tôi cần làm gì tiếp theo để hồ sơ đi tiếp?",
)
PLAN_FIELDS = ("lot_value", "cash_on_hand", "supplier_schedule", "sell_weeks", "requested_amount")
PDF_FILE = "03_sao-ke-tai-khoan.pdf"
SCAN_FILE = "03c_sao-ke-ban-scan.pdf"
TERMINAL_JOB = {"SUCCEEDED", "FAILED"}
REVIEW_STATES = {"PENDING_REVIEW", "ESCALATED"}


class FlowError(Exception):
    pass


@dataclass
class Step:
    name: str
    ok: bool
    seconds: float
    detail: dict = field(default_factory=dict)


class Api:
    def __init__(self, base: str, passcode: str, timeout: float):
        self.http = httpx.Client(base_url=base.rstrip("/"), timeout=timeout)
        self.passcode = passcode
        self.tokens: dict[tuple[str, str], str] = {}

    def token(self, subject: str, role: str) -> str:
        key = (subject, role)
        if key not in self.tokens:
            body = {"subject": subject, "role": role, "passcode": self.passcode}
            self.tokens[key] = self.expect(self.http.post("/v1/auth/token", json=body))[
                "access_token"
            ]
        return self.tokens[key]

    def call(self, method: str, path: str, actor: tuple[str, str], **kw) -> dict:
        headers = {"Authorization": f"Bearer {self.token(*actor)}", **kw.pop("headers", {})}
        return self.expect(self.http.request(method, path, headers=headers, **kw))

    @staticmethod
    def expect(response: httpx.Response) -> dict:
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            raise FlowError(
                f"{response.request.method} {response.request.url.path} "
                f"{response.status_code} {json.dumps(body, ensure_ascii=False)[:300]}"
            )
        return body


class Uploader(ABC):
    @abstractmethod
    def send(self, api: Api, case: dict, actor: tuple, form: dict, file: Path) -> dict: ...


class SyncUploader(Uploader):
    def send(self, api, case, actor, form, file):
        files = {"file": (file.name, file.read_bytes())}
        path = f"/v1/cases/{case['case_id']}/statements/upload"
        return {"case": api.call("POST", path, actor, data=form, files=files)}


class JobUploader(Uploader):
    def __init__(self, poll_seconds: float, max_wait: float):
        self.poll_seconds, self.max_wait = poll_seconds, max_wait

    def send(self, api, case, actor, form, file):
        files = {"file": (file.name, file.read_bytes())}
        path = f"/v1/cases/{case['case_id']}/statements/upload-jobs"
        job = api.call("POST", path, actor, data=form, files=files)
        deadline = time.monotonic() + self.max_wait
        while job["status"] not in TERMINAL_JOB:
            if time.monotonic() > deadline:
                raise FlowError(f"job {job['job_id']} còn {job['status']} sau {self.max_wait}s")
            time.sleep(self.poll_seconds)
            job = api.call("GET", f"/v1/jobs/{job['job_id']}", actor)
        if job["status"] != "SUCCEEDED":
            raise FlowError(f"job FAILED {job.get('error')}")
        return {"job": {k: job.get(k) for k in ("status", "attempts", "error")}}


class DossierFlow:
    def __init__(
        self, api: Api, folder: Path, spec: dict, uploader: Uploader, reviewer: str, ocr: bool
    ):
        self.api, self.folder, self.spec, self.uploader = api, folder, spec, uploader
        self.ocr = ocr
        self.expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        self.seller = (spec["seller_id"], "seller")
        self.bank = (reviewer, "underwriter")
        self.steps: list[Step] = []
        self.case: dict = {}

    def step(self, name: str, fn) -> dict | None:
        start = time.monotonic()
        try:
            detail = fn() or {}
            ok = detail.pop("_ok", True)
        except (FlowError, httpx.HTTPError, KeyError, ValueError) as exc:
            detail, ok = {"error": str(exc)}, False
        self.steps.append(Step(name, ok, round(time.monotonic() - start, 2), detail))
        return detail if ok else None

    def run(self) -> dict:
        plan = self.step("1 chat ra phương án", self.extract)
        if self.step("2 tạo hồ sơ", lambda: self.create(plan or {})) is None:
            return self.report()
        self.step("3 kết quả khi tạo", lambda: self.check("created"))
        if self.case["status"] == "AWAITING_STATEMENT":
            self.step("4 nộp sao kê", self.upload)
            self.step("5 kết quả sau sao kê", lambda: self.check("after_statement"))
        self.step("6 Copilot trả lời seller", self.ask)
        self.step("7 tờ trình và CIC", self.memo)
        if self.case["status"] in REVIEW_STATES:
            self.step("8 người duyệt quyết định", self.decide)
        if self.case["status"] == "ACCEPTED":
            self.step("9 bàn giao LOS", self.handoff)
        self.step("10 seller xem kết quả", self.seller_view)
        if self.ocr and "after_statement" in self.expected:
            self.step("11 OCR sao kê bản scan", self.ocr_check)
        return self.report()

    def ocr_check(self) -> dict:
        main = self.case
        try:
            self.create({})
            out = self.upload(self.folder / SCAN_FILE)
            want = self.expected["after_statement"]["status"]
            return {"_ok": out["status"] == want, "want_status": want, **out}
        finally:
            self.case = main

    def extract(self) -> dict:
        body = {
            "seller_id": self.spec["seller_id"],
            "shop_id": self.spec["seller_id"],
            "message": self.spec["chat"],
        }
        out = self.api.call("POST", "/v1/copilot/extract-plan", self.seller, json=body)
        draft = out["draft"]
        wrong = [k for k in PLAN_FIELDS if draft.get(k) != self.spec["plan"][k]]
        return {
            "_ok": out["ready"] and not wrong,
            "ready": out["ready"],
            "fields_wrong": wrong,
            "missing": out["missing_fields"],
            "source": out.get("source"),
            "model": out.get("model"),
            "plan": {k: draft.get(k) for k in PLAN_FIELDS},
        }

    def create(self, extracted: dict) -> dict:
        used = "chat" if not extracted.get("fields_wrong") and extracted.get("ready") else "form"
        plan = extracted["plan"] if used == "chat" else self.spec["plan"]
        body = {"seller_id": self.spec["seller_id"], "shop_id": self.spec["seller_id"], **plan}
        headers = {"Idempotency-Key": f"flow-{uuid.uuid4()}"}
        self.case = self.api.call("POST", "/v1/cases", self.seller, json=body, headers=headers)
        return {"plan_from": used, "case_id": self.case["case_id"], "status": self.case["status"]}

    def detail(self) -> dict:
        return self.api.call("GET", f"/v1/cases/{self.case['case_id']}", self.bank)

    def check(self, stage: str) -> dict:
        want = self.expected[stage]
        run = self.detail()["runs"][-1]
        cf, risk, cic = run["cashflow"], run.get("risk") or {}, run.get("cic") or {}
        statement = run.get("statement") or {}
        got = {
            "status": self.case_status(),
            "amount_proposed": cf.get("amount_proposed"),
            "tenor_weeks": cf.get("tenor_weeks"),
            "pd_bps": risk.get("pd_bps"),
            "cic_group": cic.get("bureau_group"),
            "matched": statement.get("matched") if statement else None,
        }
        expect = {
            "status": want["status"],
            "amount_proposed": want["amount_proposed"],
            "tenor_weeks": want["tenor_weeks"],
            "pd_bps": want["pd_bps"],
            "cic_group": want["cic_group"],
            "matched": (want.get("statement") or {}).get("matched"),
        }
        diff = {k: {"got": got[k], "want": expect[k]} for k in got if got[k] != expect[k]}
        return {"_ok": got["status"] == expect["status"], "got": got, "diff": diff}

    def case_status(self) -> str:
        detail = self.detail()
        self.case.update(status=detail["status"], version=detail["version"])
        return detail["status"]

    def upload(self, file: Path | None = None) -> dict:
        start, end = self.expected_period()
        form = {
            "expected_version": str(self.case["version"]),
            "period_start": start,
            "period_end": end,
        }
        file = file or self.folder / PDF_FILE
        out = self.uploader.send(self.api, self.case, self.seller, form, file)
        statement = self.detail()["runs"][-1].get("statement") or {}
        extraction = statement.get("extraction") or {}
        return {
            **out,
            "status": self.case_status(),
            "parser": extraction.get("parser"),
            "classifier": statement.get("classifier"),
            "marketplace_total": statement.get("marketplace_total"),
            "platform_total": statement.get("platform_total"),
            "matched": statement.get("matched"),
        }

    def expected_period(self) -> tuple[str, str]:
        period = self.expected["period"]
        return period["start"], period["end"]

    def ask(self) -> dict:
        path = f"/v1/cases/{self.case['case_id']}/copilot/ask"
        answers = []
        for question in QUESTIONS:
            turn = self.api.call("POST", path, self.seller, json={"question": question})
            answers.append(
                {
                    "q": question,
                    "a": turn["answer"],
                    "source": turn["source"],
                    "model": turn["model"],
                    "guard": turn["guard_codes"],
                }
            )
        history = self.api.call(
            "GET", f"/v1/cases/{self.case['case_id']}/copilot/history", self.seller
        )
        return {
            "_ok": len(history["turns"]) >= len(QUESTIONS),
            "answers": answers,
            "history_turns": len(history["turns"]),
        }

    def memo(self) -> dict:
        detail = self.detail()
        run = detail["runs"][-1]
        doc = detail["documents"][-1] if detail["documents"] else {}
        cic = run.get("cic") or {}
        return {
            "_ok": bool(doc) and doc.get("validated", False),
            "memo_generator": doc.get("generator"),
            "memo_model": doc.get("model"),
            "memo_validated": doc.get("validated"),
            "memo_errors": doc.get("validation_errors"),
            "memo_first": [s["text"] for s in doc.get("underwriter_memo", [])[:3]],
            "cic_source": cic.get("source"),
            "cic_group": cic.get("bureau_group"),
            "cic_worst_12m": cic.get("worst_group_12m"),
            "gate": run.get("cic_gate"),
        }

    def decide(self) -> dict:
        status = self.case_status()
        if status == "PENDING_REVIEW":
            body = {"decision_type": "ACCEPT_PROPOSAL", "reason": "Đủ điều kiện theo tờ trình"}
        else:
            body = {"decision_type": "REJECT", "reason": "Sao kê không khớp số liệu sàn"}
        body["expected_version"] = self.case["version"]
        out = self.api.call(
            "POST",
            f"/v1/cases/{self.case['case_id']}/decisions",
            self.bank,
            json=body,
            headers={"Idempotency-Key": f"flow-{uuid.uuid4()}"},
        )
        self.case.update(status=out["status"], version=out["version"])
        return {
            "decision": body["decision_type"],
            "status": out["status"],
            "final_amount": out["decision"].get("final_amount"),
        }

    def handoff(self) -> dict:
        body = {"expected_version": self.case["version"]}
        out = self.api.call(
            "POST", f"/v1/cases/{self.case['case_id']}/handoff", self.bank, json=body
        )
        handoff = out["handoff"]
        return {"_ok": handoff.get("status") == "SUCCEEDED", **handoff}

    def seller_view(self) -> dict:
        view = self.api.call("GET", f"/v1/cases/{self.case['case_id']}/seller-view", self.seller)
        return {
            "status": view["status"],
            "explanation": [s["text"] for s in view["explanation"][:2]],
            "decision": view["decision"],
        }

    def report(self) -> dict:
        return {
            "code": self.spec["code"],
            "seller": self.spec["seller_id"],
            "title": self.spec["title"],
            "ok": all(s.ok for s in self.steps),
            "steps": [asdict(s) for s in self.steps],
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("RIVERBANK_BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--dossiers", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--upload", choices=("job", "sync"), default="job")
    ap.add_argument("--reviewer", default="uw-flow")
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--out", default="flow-report.json")
    args = ap.parse_args()
    passcode = os.getenv("RIVERBANK_PASSCODE")
    if not passcode:
        sys.exit("thiếu biến môi trường RIVERBANK_PASSCODE")
    spec = json.loads((ROOT / "eval/dossiers/cases.json").read_text(encoding="utf-8"))
    uploader = JobUploader(1.0, args.timeout) if args.upload == "job" else SyncUploader()
    api = Api(args.base, passcode, args.timeout)
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    reports = []
    for item in spec["cases"]:
        if only and item["code"] not in only:
            continue
        folder = Path(args.dossiers) / f"{item['code']}-{item['seller_id']}"
        report = DossierFlow(api, folder, item, uploader, args.reviewer, args.ocr).run()
        reports.append(report)
        marks = " ".join(
            f"{'✓' if s['ok'] else '✗'}{s['name'].split()[0]}" for s in report["steps"]
        )
        print(f"{report['code']} {report['seller']:7} {'OK ' if report['ok'] else 'LỖI'} {marks}")
        for s in report["steps"]:
            if not s["ok"]:
                print(f"    ✗ {s['name']}: {json.dumps(s['detail'], ensure_ascii=False)[:400]}")
    Path(args.out).write_text(json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"report: {args.out}")


if __name__ == "__main__":
    main()
