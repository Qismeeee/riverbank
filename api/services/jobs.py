import logging
from abc import ABC, abstractmethod
from contextlib import AbstractContextManager, contextmanager, nullcontext
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Event, RLock
from typing import Any, Callable, Iterator
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
    def complete(self, job_id: str, result: dict, attempt: int) -> bool: ...

    @abstractmethod
    def fail(self, job_id: str, error: dict, retry: bool, attempt: int) -> bool: ...

    @abstractmethod
    def committing(
        self, job: Job, connection: Any = None
    ) -> AbstractContextManager[Callable[[], None]]: ...

    @abstractmethod
    def metrics(self) -> dict: ...


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

    def _owns(self, slot: _Slot, attempt: int) -> bool:
        return (
            slot.job.status == RUNNING
            and slot.job.attempts == attempt
            and slot.lease_until is not None
            and slot.lease_until > self.clock()
        )

    def complete(self, job_id: str, result: dict, attempt: int) -> bool:
        with self._lock:
            if not self._owns(self._slots[job_id], attempt):
                return False
            self._finish(self._slots[job_id], SUCCEEDED, result=deepcopy(result))
            return True

    def fail(self, job_id: str, error: dict, retry: bool, attempt: int) -> bool:
        with self._lock:
            slot = self._slots[job_id]
            if not self._owns(slot, attempt):
                return False
            if retry and slot.job.attempts < slot.job.max_attempts:
                slot.job = replace(
                    slot.job, status=QUEUED, error=error, updated_at=self.clock().isoformat()
                )
                slot.lease_until = None
            else:
                self._finish(slot, FAILED, error=error)
            return True

    def _finish(self, slot: _Slot, status: str, **fields) -> None:
        slot.job = replace(slot.job, status=status, updated_at=self.clock().isoformat(), **fields)
        slot.content, slot.lease_until = None, None

    @contextmanager
    def committing(self, job: Job, connection: Any = None) -> Iterator[Callable[[], None]]:
        with self._lock:

            def validate():
                if not self._owns(self._slots[job.job_id], job.attempts):
                    raise WorkflowError("JOB_LEASE_LOST", 409)

            validate()
            yield validate

    def metrics(self) -> dict:
        with self._lock:
            now = self.clock()
            jobs = [slot.job for slot in self._slots.values()]
            counts = {
                state: sum(job.status == state for job in jobs)
                for state in (QUEUED, RUNNING, SUCCEEDED, FAILED)
            }
            counts["expired_leases"] = sum(
                slot.job.status == RUNNING and slot.lease_until <= now
                for slot in self._slots.values()
            )
            counts["retried"] = sum(job.attempts > 1 for job in jobs)
            counts["recovered"] = sum(job.attempts > 1 and job.status == SUCCEEDED for job in jobs)
            queued = [
                (now - datetime.fromisoformat(job.created_at)).total_seconds()
                for job in jobs
                if job.status == QUEUED
            ]
            counts["oldest_queued_seconds"] = max(0, max(queued)) if queued else None
            return counts


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

    def complete(self, job_id: str, result: dict, attempt: int) -> bool:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            updated = conn.execute(
                "update jobs set status='SUCCEEDED',result=%s,content=null,lease_until=null,"
                "updated_at=now() where id=%s and status='RUNNING' and attempts=%s "
                "and lease_until>clock_timestamp() returning id",
                (Jsonb(result), job_id, attempt),
            )
            return updated.fetchone() is not None

    def fail(self, job_id: str, error: dict, retry: bool, attempt: int) -> bool:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            updated = conn.execute(
                "update jobs set error=%s,updated_at=now(),lease_until=null,"
                "status=case when %s and attempts<max_attempts then 'QUEUED' else 'FAILED' end,"
                "content=case when %s and attempts<max_attempts then content else null end "
                "where id=%s and status='RUNNING' and attempts=%s "
                "and lease_until>clock_timestamp() returning id",
                (Jsonb(error), retry, retry, job_id, attempt),
            )
            return updated.fetchone() is not None

    @contextmanager
    def committing(self, job: Job, connection: Any = None) -> Iterator[Callable[[], None]]:
        with self._connect() if connection is None else nullcontext(connection) as conn:

            def validate():
                found = (
                    conn.execute(
                        "select id from jobs where id=%s and status='RUNNING' "
                        "and attempts=%s and lease_until>clock_timestamp() for update",
                        (job.job_id, job.attempts),
                    ).fetchone()
                    is not None
                )
                if not found:
                    raise WorkflowError("JOB_LEASE_LOST", 409)

            validate()
            yield validate

    def metrics(self) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "select count(*) filter (where status='QUEUED'), "
                "count(*) filter (where status='RUNNING'), "
                "count(*) filter (where status='SUCCEEDED'), "
                "count(*) filter (where status='FAILED'), count(*) filter (where status='RUNNING' "
                "and lease_until<=clock_timestamp()), count(*) filter (where attempts>1), "
                "count(*) filter (where attempts>1 and status='SUCCEEDED'), "
                "max(extract(epoch from clock_timestamp()-created_at)) "
                "filter (where status='QUEUED') "
                "from jobs"
            ).fetchone()
        names = (QUEUED, RUNNING, SUCCEEDED, FAILED, "expired_leases", "retried", "recovered")
        return {
            **dict(zip(names, row[:7], strict=True)),
            "oldest_queued_seconds": max(0, float(row[7])) if row[7] is not None else None,
        }


class JobHandler(ABC):
    kind: str

    @abstractmethod
    def handle(self, job: Job, content: bytes) -> dict: ...

    def run(self, job: Job, content: bytes, commit_guard: Callable) -> dict:
        return self.handle(job, content)


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
    ):
        self.queue = queue
        self.handlers = {h.kind: h for h in handlers}
        self.cfg = cfg or JobConfig()

    def run_once(self) -> bool:
        claim = self.queue.claim(self.cfg.lease_seconds)
        if claim is None:
            return False
        job = claim.job
        handler = self.handlers.get(job.kind)
        if handler is None:
            self.queue.fail(
                job.job_id, {"code": "JOB_KIND_UNKNOWN", "status": 500}, False, job.attempts
            )
            return True
        try:
            result = handler.run(
                job, claim.content, lambda conn=None: self.queue.committing(job, conn)
            )
        except Exception as exc:
            error, retry = _error(exc)
            log.warning("job %s attempt %s failed: %s", job.job_id, job.attempts, error["code"])
            self.queue.fail(job.job_id, error, retry, job.attempts)
        else:
            if not self.queue.complete(job.job_id, result, job.attempts):
                log.warning("job %s attempt %s lost its lease", job.job_id, job.attempts)
        return True

    def run(self, stop: Event) -> None:
        while not stop.is_set():
            try:
                busy = self.run_once()
            except Exception:
                log.exception("job worker loop failed")
                busy = False
            if not busy:
                stop.wait(self.cfg.idle_seconds)
