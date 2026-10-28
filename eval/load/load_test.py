import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import cycle
from pathlib import Path
from threading import Event, Lock

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval/dossiers"))

from flow_check import (  # noqa: E402
    PDF_FILE,
    REVIEW_STATES,
    Api,
    DossierFlow,
    JobUploader,
)

ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PERCENTILES = (50, 95, 99)


@dataclass(frozen=True)
class Sample:
    at: float
    stage: int
    route: str
    status: int
    seconds: float


@dataclass(frozen=True)
class Stage:
    users: int
    seconds: float

    @classmethod
    def parse(cls, text: str) -> list["Stage"]:
        stages = []
        for part in text.split(","):
            users, seconds = part.split(":")
            stages.append(cls(int(users), float(seconds)))
        return stages


class Recorder:
    def __init__(self):
        self.samples: list[Sample] = []
        self.flows: list[dict] = []
        self.stage = 0
        self._lock = Lock()

    def request(self, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self.samples.append(Sample(time.time(), self.stage, route, status, seconds))

    def flow(self, report: dict, seconds: float) -> None:
        with self._lock:
            self.flows.append(
                {**report, "stage": self.stage, "seconds": round(seconds, 2)}
            )


class TimedApi(Api):
    def __init__(self, base: str, passcode: str, timeout: float, recorder: Recorder):
        super().__init__(base, passcode, timeout)
        self.recorder = recorder
        self.http.event_hooks["response"] = [self._record]

    def _record(self, response: httpx.Response) -> None:
        response.read()
        route = f"{response.request.method} {ID.sub('{id}', response.request.url.path)}"
        self.recorder.request(
            route, response.status_code, response.elapsed.total_seconds()
        )


class LoadFlow(DossierFlow):
    def __init__(self, *args, llm: bool, **kwargs):
        super().__init__(*args, **kwargs)
        self.llm = llm

    def run(self) -> dict:
        plan = self.step("1 chat ra phương án", self.extract) if self.llm else None
        if self.step("2 tạo hồ sơ", lambda: self.create(plan or {})) is None:
            return self.report()
        self.step("3 kết quả khi tạo", lambda: self.check("created"))
        if self.case["status"] == "AWAITING_STATEMENT":
            self.step("4 nộp sao kê", self.upload)
            self.step("5 kết quả sau sao kê", lambda: self.check("after_statement"))
        if self.llm:
            self.step("6 Copilot trả lời seller", self.ask)
        if self.case["status"] in REVIEW_STATES:
            self.step("8 người duyệt quyết định", self.decide)
        if self.case["status"] == "ACCEPTED":
            self.step("9 bàn giao LOS", self.handoff)
        return self.report()


class LoadTest:
    def __init__(self, args, specs: list[dict], passcode: str):
        self.args, self.specs, self.passcode = args, specs, passcode
        self.recorder = Recorder()
        self.uploader = JobUploader(1.0, args.timeout)

    def user(self, index: int, stop: Event) -> None:
        api = TimedApi(self.args.base, self.passcode, self.args.timeout, self.recorder)
        specs = cycle(
            self.specs[index % len(self.specs) :]
            + self.specs[: index % len(self.specs)]
        )
        while not stop.is_set():
            spec = next(specs)
            folder = Path(self.args.dossiers) / f"{spec['code']}-{spec['seller_id']}"
            start = time.monotonic()
            flow = LoadFlow(
                api,
                folder,
                spec,
                self.uploader,
                self.args.reviewer,
                False,
                llm=self.args.llm,
            )
            try:
                report = flow.run()
            except Exception as exc:
                report = {
                    "code": spec["code"],
                    "ok": False,
                    "steps": [],
                    "error": repr(exc),
                }
            self.recorder.flow(report, time.monotonic() - start)
            stop.wait(self.args.think)

    def run(self) -> dict:
        started = datetime.now(timezone.utc)
        for number, stage in enumerate(Stage.parse(self.args.stages), start=1):
            self.recorder.stage = number
            stop = Event()
            print(
                f"nấc {number}: {stage.users} người dùng trong {stage.seconds:.0f}s",
                flush=True,
            )
            with ThreadPoolExecutor(stage.users) as pool:
                for index in range(stage.users):
                    pool.submit(self.user, index, stop)
                stop.wait(stage.seconds)
                stop.set()
            print(summary_line(self.recorder, number), flush=True)
        return {
            "base": self.args.base,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "stages": [asdict(s) for s in Stage.parse(self.args.stages)],
            "llm": self.args.llm,
            "summary": summarize(self.recorder),
            "flows": self.recorder.flows,
            "samples": [asdict(s) for s in self.recorder.samples],
        }


def percentile(values: list[float], p: int) -> float:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(p / 100 * len(ordered) + 0.5) - 1))
    return round(ordered[rank], 3)


def stats(samples: list[Sample]) -> dict:
    seconds = [s.seconds for s in samples]
    span = (
        max(s.at for s in samples) - min(s.at for s in samples)
        if len(samples) > 1
        else 0
    )
    codes = defaultdict(int)
    for s in samples:
        codes[str(s.status)] += 1
    errors = sum(n for code, n in codes.items() if int(code) >= 500)
    rejected = sum(n for code, n in codes.items() if int(code) == 429)
    return {
        "requests": len(samples),
        "rps": round(len(samples) / span, 2) if span else None,
        "error_rate_5xx": round(errors / len(samples), 4),
        "rate_limited_429": rejected,
        "codes": dict(codes),
        **{f"p{p}": percentile(seconds, p) for p in PERCENTILES},
        "max": round(max(seconds), 3),
    }


def summarize(recorder: Recorder) -> dict:
    out = {}
    for stage in sorted({s.stage for s in recorder.samples}):
        samples = [s for s in recorder.samples if s.stage == stage]
        routes = defaultdict(list)
        for s in samples:
            routes[s.route].append(s)
        flows = [f for f in recorder.flows if f["stage"] == stage]
        out[str(stage)] = {
            "all": stats(samples),
            "routes": {route: stats(items) for route, items in sorted(routes.items())},
            "flows": {
                "done": len(flows),
                "ok": sum(f["ok"] for f in flows),
                **(
                    {
                        f"p{p}_seconds": percentile([f["seconds"] for f in flows], p)
                        for p in PERCENTILES
                    }
                    if flows
                    else {}
                ),
            },
        }
    return out


def summary_line(recorder: Recorder, stage: int) -> str:
    total = summarize(recorder).get(str(stage))
    if not total:
        return "  không có request nào"
    a, f = total["all"], total["flows"]
    return (
        f"  {a['requests']} request, {a['rps']} rps, p95 {a['p95']}s, p99 {a['p99']}s, "
        f"5xx {a['error_rate_5xx']:.2%}, 429 {a['rate_limited_429']}, "
        f"luồng xong {f['done']} (đúng {f['ok']})"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--base", default=os.getenv("RIVERBANK_BASE_URL", "http://127.0.0.1:8000")
    )
    ap.add_argument("--dossiers", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--stages", default="2:60,5:120,10:120")
    ap.add_argument("--think", type=float, default=1.0)
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--reviewer", default="tran.ngoclan")
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--out", default="load-report.json")
    args = ap.parse_args()
    passcode = os.getenv("RIVERBANK_PASSCODE")
    if not passcode:
        sys.exit("thiếu biến môi trường RIVERBANK_PASSCODE")
    spec = json.loads((ROOT / "eval/dossiers/cases.json").read_text(encoding="utf-8"))
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    specs = [c for c in spec["cases"] if not only or c["code"] in only]
    missing = [
        c["code"]
        for c in specs
        if not (
            Path(args.dossiers) / f"{c['code']}-{c['seller_id']}" / PDF_FILE
        ).exists()
    ]
    if missing:
        sys.exit(f"thiếu thư mục hồ sơ: {', '.join(missing)}")
    report = LoadTest(args, specs, passcode).run()
    Path(args.out).write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
