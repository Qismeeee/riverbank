import logging
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Event, RLock
from typing import Callable
from uuid import uuid4

from pydantic import BaseModel

from services.underwriting.models import WorkflowError

log = logging.getLogger("riverbank.jobs")

QUEUED, RUNNING, SUCCEEDED, FAILED = "QUEUED", "RUNNING", "SUCCEEDED", "FAILED"
LEASE_EXPIRED = {"code": "JOB_LEASE_EXPIRED", "status": 500, "details": []}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class JobConfig:
    max_attempts: int = 3
    lease_seconds: int = 600
    idle_seconds: float = 2.0


@dataclass(frozen=True)
class Job:
    job_id: str
    kind: str
    case_id: str
    submitted_by: str
    payload: dict
    status: str = QUEUED
    attempts: int = 0
    max_attempts: int = JobConfig.max_attempts
    result: dict | None = None
    error: dict | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def new(cls, kind: str, case_id: str, submitted_by: str, payload: dict, cfg: JobConfig):
        now = utc_now().isoformat()
        return cls(
            str(uuid4()),
            kind,
            case_id,
            submitted_by,
            payload,
            max_attempts=cfg.max_attempts,
            created_at=now,
            updated_at=now,
        )

    def view(self) -> dict:
        return asdict(self)


class JobView(BaseModel):
    job_id: str
    kind: str
    case_id: str
    submitted_by: str
    payload: dict
    status: str
    attempts: int
    max_attempts: int
    result: dict | None
    error: dict | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Claim:
    job: Job
    content: bytes


class JobQueue(ABC):
    @abstractmethod
    def enqueue(self, job: Job, content: bytes) -> Job: ...

    @abstractmethod
    def get(self, job_id: str) -> Job: ...

    @abstractmethod
    def claim(self, lease_seconds: int) -> Claim | None: ...

    @abstractmethod
    def complete(self, job_id: str, result: dict) -> None: ...

    @abstractmethod
    def fail(self, job_id: str, error: dict, retry: bool) -> None: ...


@dataclass
class _Slot:
    job: Job
    content: bytes | None
    lease_until: datetime | None = None


class MemoryJobQueue(JobQueue):
    def __init__(self, clock: Callable[[], datetime] = utc_now) -> None:
        self.clock = clock
        self._slots: dict[str, _Slot] = {}
        self._lock = RLock()

    def enqueue(self, job: Job, content: bytes) -> Job:
        with self._lock:
            self._slots[job.job_id] = _Slot(job, content)
            return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            if job_id not in self._slots:
                raise WorkflowError("JOB_NOT_FOUND", 404)
            return self._slots[job_id].job

    def claim(self, lease_seconds: int) -> Claim | None:
        now = self.clock()
        with self._lock:
            for slot in self._slots.values():
                expired = slot.job.status == RUNNING and slot.lease_until <= now
                if expired and slot.job.attempts >= slot.job.max_attempts:
                    self._finish(slot, FAILED, error=LEASE_EXPIRED)
            for slot in sorted(self._slots.values(), key=lambda s: s.job.created_at):
                job = slot.job
                if job.status == QUEUED or (job.status == RUNNING and slot.lease_until <= now):
                    slot.job = replace(
                        job, status=RUNNING, attempts=job.attempts + 1, updated_at=now.isoformat()
                    )
                    slot.lease_until = now + timedelta(seconds=lease_seconds)
                    return Claim(slot.job, slot.content)
        return None

    def complete(self, job_id: str, result: dict) -> None:
        with self._lock:
            self._finish(self._slots[job_id], SUCCEEDED, result=deepcopy(result))

    def fail(self, job_id: str, error: dict, retry: bool) -> None:
        with self._lock:
            slot = self._slots[job_id]
            if retry and slot.job.attempts < slot.job.max_attempts:
                slot.job = replace(
                    slot.job, status=QUEUED, error=error, updated_at=self.clock().isoformat()
                )
                slot.lease_until = None
            else:
                self._finish(slot, FAILED, error=error)

    def _finish(self, slot: _Slot, status: str, **fields) -> None:
        slot.job = replace(slot.job, status=status, updated_at=self.clock().isoformat(), **fields)
        slot.content, slot.lease_until = None, None


COLUMNS = (
    "id,kind,case_id,submitted_by,payload,status,attempts,max_attempts,result,error,"
    "created_at,updated_at"
)


def _job(row) -> Job:
    return Job(
        str(row[0]),
        row[1],
        str(row[2]),
        row[3],
        row[4],
        row[5],
        row[6],
        row[7],
        row[8],
        row[9],
        row[10].isoformat(),
        row[11].isoformat(),
    )


class PostgresJobQueue(JobQueue):
    def __init__(self, database_url: str):
        self.database_url = database_url

    def _connect(self):
        from db.pool import connect

        return connect(self.database_url)

    def enqueue(self, job: Job, content: bytes) -> Job:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            row = conn.execute(
                "insert into jobs (id,kind,case_id,submitted_by,payload,max_attempts,content) "
                f"values (%s,%s,%s,%s,%s,%s,%s) returning {COLUMNS}",
                (
                    job.job_id,
                    job.kind,
                    job.case_id,
                    job.submitted_by,
                    Jsonb(job.payload),
                    job.max_attempts,
                    content,
                ),
            ).fetchone()
        return _job(row)

    def get(self, job_id: str) -> Job:
        with self._connect() as conn:
            row = conn.execute(f"select {COLUMNS} from jobs where id=%s", (job_id,)).fetchone()
        if not row:
            raise WorkflowError("JOB_NOT_FOUND", 404)
        return _job(row)

    def claim(self, lease_seconds: int) -> Claim | None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            conn.execute(
                "update jobs set status='FAILED',error=%s,content=null,lease_until=null,"
                "updated_at=now() where status='RUNNING' and lease_until<=now() "
                "and attempts>=max_attempts",
                (Jsonb(LEASE_EXPIRED),),
            )
            row = conn.execute(
                "update jobs set status='RUNNING',attempts=attempts+1,updated_at=now(),"
                "lease_until=now()+make_interval(secs=>%s) where id=(select id from jobs "
                "where status='QUEUED' or (status='RUNNING' and lease_until<=now()) "
                "order by created_at for update skip locked limit 1) "
                f"returning {COLUMNS},content",
                (lease_seconds,),
            ).fetchone()
        return Claim(_job(row), bytes(row[12])) if row else None

    def complete(self, job_id: str, result: dict) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            conn.execute(
                "update jobs set status='SUCCEEDED',result=%s,content=null,lease_until=null,"
                "updated_at=now() where id=%s and status='RUNNING'",
                (Jsonb(result), job_id),
            )

    def fail(self, job_id: str, error: dict, retry: bool) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            conn.execute(
                "update jobs set error=%s,updated_at=now(),lease_until=null,"
                "status=case when %s and attempts<max_attempts then 'QUEUED' else 'FAILED' end,"
                "content=case when %s and attempts<max_attempts then content else null end "
                "where id=%s and status='RUNNING'",
                (Jsonb(error), retry, retry, job_id),
            )


class JobHandler(ABC):
    kind: str

    @abstractmethod
    def handle(self, job: Job, content: bytes) -> dict: ...


def _error(exc: Exception) -> tuple[dict, bool]:
    if isinstance(exc, WorkflowError):
        return {"code": exc.code, "status": exc.status, "details": exc.details}, exc.status >= 500
    return {"code": "JOB_HANDLER_ERROR", "status": 500, "details": [type(exc).__name__]}, True


class JobWorker:
    def __init__(
        self,
        queue: JobQueue,
        handlers: list[JobHandler],
        cfg: JobConfig | None = None,
        wake: Event | None = None,
    ):
        self.queue = queue
        self.handlers = {h.kind: h for h in handlers}
        self.cfg = cfg or JobConfig()
        self.wake = wake or Event()

    def run_once(self) -> bool:
        claim = self.queue.claim(self.cfg.lease_seconds)
        if claim is None:
            return False
        job = claim.job
        handler = self.handlers.get(job.kind)
        if handler is None:
            self.queue.fail(job.job_id, {"code": "JOB_KIND_UNKNOWN", "status": 500}, False)
            return True
        try:
            result = handler.handle(job, claim.content)
        except Exception as exc:
            error, retry = _error(exc)
            log.warning("job %s attempt %s failed: %s", job.job_id, job.attempts, error["code"])
            self.queue.fail(job.job_id, error, retry)
        else:
            self.queue.complete(job.job_id, result)
        return True

    def run(self, stop: Event) -> None:
        while not stop.is_set():
            try:
                busy = self.run_once()
            except Exception:
                log.exception("job worker loop failed")
                busy = False
            if not busy:
                self.wake.wait(self.cfg.idle_seconds)
                self.wake.clear()
