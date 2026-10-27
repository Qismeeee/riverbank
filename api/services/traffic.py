import math
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from services.underwriting.models import Case, WorkflowError

ZONE = ZoneInfo("Asia/Ho_Chi_Minh")
ZONE_NAME = "Asia/Ho_Chi_Minh"
MAX_TRAFFIC_CASES = 5000
LATENCY_TARGET_MS = 90_000
COST_TARGET_MICRO_USD = 100_000


class TrafficTargets(BaseModel):
    latency_ms: int
    cost_micro_usd: int


class TrafficLatency(BaseModel):
    p50: int | None
    p95: int | None


class TrafficCost(BaseModel):
    known_cases: int
    unknown_cases: int
    total_micro_usd: int | None
    p50_micro_usd: int | None


class TrafficSummary(BaseModel):
    cases: int
    opened_today: int
    measured_runs: int
    unmeasured_runs: int
    latency_ms: TrafficLatency
    cost: TrafficCost


class TrafficDay(BaseModel):
    day: str
    cases: int


class TrafficStatus(BaseModel):
    status: str
    cases: int


class TrafficModel(BaseModel):
    model: str
    calls: int
    cases: int


class TrafficRun(BaseModel):
    run_id: str
    trigger: str
    started_at: str | None
    finished_at: str | None
    duration_ms: int | None


class TrafficRecent(BaseModel):
    case_id: str
    seller_id: str
    status: str
    reason: str | None
    created_at: str
    first_run: TrafficRun | None
    models: list[str]
    cost_micro_usd: int | None
    cost_known: bool


class TrafficReport(BaseModel):
    zone: str
    generated_at: str
    targets: TrafficTargets
    summary: TrafficSummary
    by_day: list[TrafficDay]
    by_status: list[TrafficStatus]
    by_model: list[TrafficModel]
    recent: list[TrafficRecent]


def _date(value: str) -> date:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise WorkflowError("TRAFFIC_WINDOW_INVALID", 422)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise WorkflowError("TRAFFIC_WINDOW_INVALID", 422) from None
    if parsed.isoformat() != value:
        raise WorkflowError("TRAFFIC_WINDOW_INVALID", 422)
    return parsed


def parse_window(
    window_from: str | None, window_to: str | None
) -> tuple[datetime | None, datetime | None]:
    start_day = _date(window_from) if window_from else None
    end_day = _date(window_to) if window_to else None
    if start_day and end_day and start_day > end_day:
        raise WorkflowError("TRAFFIC_WINDOW_INVALID", 422)
    start = datetime.combine(start_day, time.min, ZONE) if start_day else None
    end = datetime.combine(end_day + timedelta(days=1), time.min, ZONE) if end_day else None
    return start, end


def created_at(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=UTC)
    return stamp


def in_created_window(value: str, start: datetime | None, end: datetime | None) -> bool:
    stamp = created_at(value)
    if start is not None and stamp < start:
        return False
    if end is not None and stamp >= end:
        return False
    return True


def local_day(value: str) -> str:
    return created_at(value).astimezone(ZONE).date().isoformat()


def percentile(values: list[int], percent: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    index = min(count - 1, max(0, math.ceil(percent / 100 * count) - 1))
    return ordered[index]


def _initial(case: Case) -> dict | None:
    runs = [run for run in case.runs if run.get("trigger") == "INITIAL"]
    if not runs:
        return None
    return min(runs, key=lambda run: run.get("run_no") if type(run.get("run_no")) is int else 10**9)


def _duration(run: dict | None) -> int | None:
    if run is None:
        return None
    value = run.get("duration_ms")
    return value if type(value) is int else None


def _metered(run: dict) -> bool:
    return (
        isinstance(run.get("started_at"), str)
        and isinstance(run.get("finished_at"), str)
        and type(run.get("duration_ms")) is int
    )


def calls_of(case: Case) -> list[dict]:
    return [call for run in case.runs for call in (run.get("model_calls") or [])]


def case_cost(case: Case) -> tuple[int | None, bool]:
    if not case.runs or not all(_metered(run) for run in case.runs):
        return None, False
    amounts: list[int] = []
    for call in calls_of(case):
        amount = call.get("cost_micro_usd")
        if type(amount) is not int:
            return None, False
        amounts.append(amount)
    return sum(amounts), True


def model_names(case: Case) -> list[str]:
    calls = calls_of(case)
    if calls:
        source = [call.get("model") for call in calls]
    else:
        source = [doc.get("model") for doc in case.documents]
    names: list[str] = []
    for name in source:
        if isinstance(name, str) and name and name not in names:
            names.append(name)
    return names


def _first_run(run: dict | None) -> dict | None:
    if run is None:
        return None
    duration = run.get("duration_ms")
    return {
        "run_id": run["run_id"],
        "trigger": run["trigger"],
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "duration_ms": duration if type(duration) is int else None,
    }


def build_traffic(cases: list[Case], now: datetime, recent: int) -> dict:
    today = now.astimezone(ZONE).date().isoformat()
    durations: list[int] = []
    known: list[int] = []
    days: dict[str, int] = {}
    statuses: dict[str, int] = {}
    models: dict[str, list] = {}
    opened = 0
    for case in cases:
        day = local_day(case.created_at)
        days[day] = days.get(day, 0) + 1
        statuses[case.status] = statuses.get(case.status, 0) + 1
        if day == today:
            opened += 1
        duration = _duration(_initial(case))
        if duration is not None:
            durations.append(duration)
        amount, priced = case_cost(case)
        if priced and amount is not None:
            known.append(amount)
        for call in calls_of(case):
            name = call.get("model")
            if not isinstance(name, str) or not name:
                continue
            row = models.setdefault(name, [0, set()])
            row[0] += 1
            row[1].add(case.case_id)
    ordered = sorted(cases, key=lambda case: (case.created_at, case.case_id), reverse=True)
    recent_rows = []
    for case in ordered[:recent]:
        amount, priced = case_cost(case)
        recent_rows.append(
            {
                "case_id": case.case_id,
                "seller_id": case.seller_id,
                "status": case.status,
                "reason": case.reason,
                "created_at": case.created_at,
                "first_run": _first_run(_initial(case)),
                "models": model_names(case),
                "cost_micro_usd": amount if priced else None,
                "cost_known": priced,
            }
        )
    return {
        "zone": ZONE_NAME,
        "generated_at": now.isoformat(),
        "targets": {"latency_ms": LATENCY_TARGET_MS, "cost_micro_usd": COST_TARGET_MICRO_USD},
        "summary": {
            "cases": len(cases),
            "opened_today": opened,
            "measured_runs": len(durations),
            "unmeasured_runs": len(cases) - len(durations),
            "latency_ms": {
                "p50": percentile(durations, 50),
                "p95": percentile(durations, 95),
            },
            "cost": {
                "known_cases": len(known),
                "unknown_cases": len(cases) - len(known),
                "total_micro_usd": sum(known) if known else None,
                "p50_micro_usd": percentile(known, 50),
            },
        },
        "by_day": [{"day": day, "cases": days[day]} for day in sorted(days)],
        "by_status": [
            {"status": status, "cases": statuses[status]}
            for status in sorted(statuses, key=lambda status: (-statuses[status], status))
        ],
        "by_model": [
            {"model": name, "calls": models[name][0], "cases": len(models[name][1])}
            for name in sorted(models, key=lambda name: (-models[name][0], name))
        ],
        "recent": recent_rows,
    }
