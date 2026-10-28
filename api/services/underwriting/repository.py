from abc import ABC, abstractmethod
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from threading import RLock
from typing import Iterator

from services.traffic import in_created_window

from .models import Case, WorkflowError


def case_header(case: Case) -> dict:
    header = asdict(case)
    for key in ("runs", "evidence", "documents", "events", "decision", "handoff"):
        header.pop(key)
    return header


EVENT_SCHEMA = "case-event/v1"
EVENT_RELAY_LOCK = 70481002


def event_message(case_id: str, ordinal: int, event: dict) -> dict:
    return {"schema": EVENT_SCHEMA, "case_id": case_id, "ordinal": ordinal, **deepcopy(event)}


OPEN_STATES = ("PENDING_REVIEW", "ESCALATED", "NO_LOAN_NEEDED", "AWAITING_STATEMENT")
DECIDED_STATES = ("ACCEPTED", "REJECTED")


def queue_scope(status: str | None) -> tuple[list[str], bool]:
    if status is None:
        return list(OPEN_STATES), False
    return [status], status in DECIDED_STATES


class CaseRepository(ABC):
    @abstractmethod
    def create(self, case: Case, key: str, request_hash: str) -> Case: ...

    @abstractmethod
    def find_request(self, seller_id: str, key: str, request_hash: str) -> Case | None: ...

    @abstractmethod
    def get(self, case_id: str) -> Case: ...

    @abstractmethod
    def list_queue(self, status: str | None, limit: int, offset: int) -> list[Case]: ...

    @abstractmethod
    def list_by_seller(self, seller_id: str, limit: int, offset: int) -> list[Case]: ...

    @abstractmethod
    def count_created(self, start: datetime | None, end: datetime | None) -> int: ...

    @abstractmethod
    def list_created(self, start: datetime | None, end: datetime | None) -> list[Case]: ...

    @abstractmethod
    def transaction(self, case_id: str, version: int): ...

    @abstractmethod
    def publishing(self, limit: int): ...

    @abstractmethod
    def prune_published(self, older_than_hours: float) -> int: ...


class MemoryCaseRepository(CaseRepository):
    def __init__(self) -> None:
        self._cases: dict[str, Case] = {}
        self._requests: dict[tuple[str, str], tuple[str, str]] = {}
        self._outbox: list[dict] = []
        self._lock = RLock()

    def _queue_events(self, previous: Case | None, case: Case) -> None:
        known = {e["event_id"] for e in previous.events} if previous else set()
        self._outbox.extend(
            event_message(case.case_id, ordinal, event)
            for ordinal, event in enumerate(case.events)
            if event["event_id"] not in known
        )

    def find_request(self, seller_id: str, key: str, request_hash: str) -> Case | None:
        with self._lock:
            item = self._requests.get((seller_id, key))
            if not item:
                return None
            if item[0] != request_hash:
                raise WorkflowError("IDEMPOTENCY_KEY_REUSED")
            return self.get(item[1])

    def create(self, case: Case, key: str, request_hash: str) -> Case:
        with self._lock:
            existing = self.find_request(case.seller_id, key, request_hash)
            if existing:
                return existing
            self._cases[case.case_id] = deepcopy(case)
            self._requests[(case.seller_id, key)] = (request_hash, case.case_id)
            self._queue_events(None, case)
            return deepcopy(case)

    def get(self, case_id: str) -> Case:
        with self._lock:
            if case_id not in self._cases:
                raise WorkflowError("CASE_NOT_FOUND", 404)
            return deepcopy(self._cases[case_id])

    def list_queue(self, status: str | None, limit: int, offset: int) -> list[Case]:
        states, newest_first = queue_scope(status)
        with self._lock:
            cases = sorted(
                (c for c in self._cases.values() if c.status in states),
                key=lambda c: (c.created_at, c.case_id),
                reverse=newest_first,
            )
            return [Case(**case_header(c)) for c in cases[offset : offset + limit]]

    def list_by_seller(self, seller_id: str, limit: int, offset: int) -> list[Case]:
        with self._lock:
            cases = sorted(
                (c for c in self._cases.values() if c.seller_id == seller_id),
                key=lambda c: (c.created_at, c.case_id),
                reverse=True,
            )
            return [Case(**case_header(c)) for c in cases[offset : offset + limit]]

    def count_created(self, start: datetime | None, end: datetime | None) -> int:
        with self._lock:
            return sum(
                in_created_window(case.created_at, start, end) for case in self._cases.values()
            )

    def list_created(self, start: datetime | None, end: datetime | None) -> list[Case]:
        with self._lock:
            return deepcopy(
                [
                    case
                    for case in self._cases.values()
                    if in_created_window(case.created_at, start, end)
                ]
            )

    @contextmanager
    def transaction(self, case_id: str, version: int) -> Iterator[Case]:
        with self._lock:
            case = self.get(case_id)
            if case.version != version:
                raise WorkflowError("CASE_VERSION_CONFLICT")
            yield case
            case.version += 1
            self._queue_events(self._cases[case_id], case)
            self._cases[case_id] = deepcopy(case)

    @contextmanager
    def publishing(self, limit: int) -> Iterator[list[dict]]:
        # Publish outside the lock: a slow broker must not stall case reads and writes.
        with self._lock:
            batch = deepcopy(self._outbox[:limit])
        yield batch
        sent = {message["event_id"] for message in batch}
        with self._lock:
            self._outbox = [m for m in self._outbox if m["event_id"] not in sent]

    def prune_published(self, older_than_hours: float) -> int:
        return 0


class PostgresCaseRepository(CaseRepository):
    def __init__(self, database_url: str):
        self.database_url = database_url

    def _connect(self):
        from db.pool import connect

        return connect(self.database_url)

    @staticmethod
    def _load(conn, case_id: str, lock: bool = False) -> Case:
        row = conn.execute(
            "select header from cases where case_id=%s" + (" for update" if lock else ""),
            (case_id,),
        ).fetchone()
        if row is None:
            raise WorkflowError("CASE_NOT_FOUND", 404)
        case = Case(**row[0])
        for table, attr in (
            ("assessment_runs", "runs"),
            ("evidence", "evidence"),
            ("generated_documents", "documents"),
            ("case_events", "events"),
        ):
            rows = conn.execute(
                f"select payload from {table} where case_id=%s order by ordinal",
                (case_id,),
            ).fetchall()
            setattr(case, attr, [r[0] for r in rows])
        for table, attr in (("decisions", "decision"), ("handoffs", "handoff")):
            row = conn.execute(
                f"select payload from {table} where case_id=%s", (case_id,)
            ).fetchone()
            setattr(case, attr, row[0] if row else None)
        return case

    @staticmethod
    def _save(conn, case: Case) -> None:
        from psycopg.types.json import Jsonb

        header = case_header(case)
        conn.execute(
            "update cases set status=%s, version=%s, header=%s where case_id=%s",
            (case.status, case.version, Jsonb(header), case.case_id),
        )
        for table, items, id_key in (
            ("assessment_runs", case.runs, "run_id"),
            ("evidence", case.evidence, "evidence_id"),
            ("generated_documents", case.documents, "document_id"),
        ):
            for ordinal, item in enumerate(items):
                conn.execute(
                    f"insert into {table} (id,case_id,ordinal,payload) values (%s,%s,%s,%s) "
                    "on conflict (id) do nothing",
                    (item[id_key], case.case_id, ordinal, Jsonb(item)),
                )
        for ordinal, event in enumerate(case.events):
            inserted = conn.execute(
                "insert into case_events (id,case_id,ordinal,payload) values (%s,%s,%s,%s) "
                "on conflict (id) do nothing returning id",
                (event["event_id"], case.case_id, ordinal, Jsonb(event)),
            ).fetchone()
            if inserted:
                conn.execute(
                    "insert into event_outbox (event_id,case_id,ordinal,payload) "
                    "values (%s,%s,%s,%s)",
                    (
                        event["event_id"],
                        case.case_id,
                        ordinal,
                        Jsonb(event_message(case.case_id, ordinal, event)),
                    ),
                )
        if case.decision:
            conn.execute(
                "insert into decisions (case_id,payload) values (%s,%s) "
                "on conflict (case_id) do nothing",
                (case.case_id, Jsonb(case.decision)),
            )
        if case.handoff:
            conn.execute(
                "insert into handoffs (case_id,payload) values (%s,%s) "
                "on conflict (case_id) do update set payload=excluded.payload",
                (case.case_id, Jsonb(case.handoff)),
            )

    def find_request(self, seller_id: str, key: str, request_hash: str) -> Case | None:
        with self._connect() as conn:
            row = conn.execute(
                "select case_id,request_hash from cases where seller_id=%s and request_key=%s",
                (seller_id, key),
            ).fetchone()
            if not row:
                return None
            if row[1] != request_hash:
                raise WorkflowError("IDEMPOTENCY_KEY_REUSED")
            return self._load(conn, row[0])

    def create(self, case: Case, key: str, request_hash: str) -> Case:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            inserted = conn.execute(
                "insert into cases (case_id,seller_id,shop_id,status,version,created_at,"
                "request_key,request_hash,header) values (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "on conflict (seller_id,request_key) do nothing returning case_id",
                (
                    case.case_id,
                    case.seller_id,
                    case.shop_id,
                    case.status,
                    case.version,
                    case.created_at,
                    key,
                    request_hash,
                    Jsonb(case_header(case)),
                ),
            ).fetchone()
            if inserted:
                self._save(conn, case)
                return deepcopy(case)
            row = conn.execute(
                "select case_id,request_hash from cases where seller_id=%s and request_key=%s",
                (case.seller_id, key),
            ).fetchone()
            if row[1] != request_hash:
                raise WorkflowError("IDEMPOTENCY_KEY_REUSED")
            return self._load(conn, row[0])

    def get(self, case_id: str) -> Case:
        with self._connect() as conn:
            return self._load(conn, case_id)

    def list_queue(self, status: str | None, limit: int, offset: int) -> list[Case]:
        states, newest_first = queue_scope(status)
        order = "created_at desc,case_id desc" if newest_first else "created_at,case_id"
        with self._connect() as conn:
            rows = conn.execute(
                f"select header from cases where status = any(%s) "
                f"order by {order} limit %s offset %s",
                (states, limit, offset),
            ).fetchall()
            return [Case(**row[0]) for row in rows]

    def list_by_seller(self, seller_id: str, limit: int, offset: int) -> list[Case]:
        with self._connect() as conn:
            rows = conn.execute(
                "select header from cases where seller_id=%s "
                "order by created_at desc,case_id desc limit %s offset %s",
                (seller_id, limit, offset),
            ).fetchall()
            return [Case(**row[0]) for row in rows]

    def count_created(self, start: datetime | None, end: datetime | None) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "select count(*) from cases where "
                "(%s::timestamptz is null or created_at >= %s) and "
                "(%s::timestamptz is null or created_at < %s)",
                (start, start, end, end),
            ).fetchone()
            return row[0]

    def list_created(self, start: datetime | None, end: datetime | None) -> list[Case]:
        with self._connect() as conn:
            rows = conn.execute(
                "select case_id from cases where "
                "(%s::timestamptz is null or created_at >= %s) and "
                "(%s::timestamptz is null or created_at < %s) "
                "order by created_at desc, case_id desc",
                (start, start, end, end),
            ).fetchall()
            return [self._load(conn, row[0]) for row in rows]

    @contextmanager
    def transaction(self, case_id: str, version: int) -> Iterator[Case]:
        with self._connect() as conn:
            case = self._load(conn, case_id, lock=True)
            if case.version != version:
                raise WorkflowError("CASE_VERSION_CONFLICT")
            yield case
            case.version += 1
            self._save(conn, case)

    @contextmanager
    def publishing(self, limit: int) -> Iterator[list[dict]]:
        with self._connect() as conn:
            locked = conn.execute(
                "select pg_try_advisory_xact_lock(%s)", (EVENT_RELAY_LOCK,)
            ).fetchone()[0]
            if not locked:
                yield []
                return
            rows = conn.execute(
                "select event_id,payload from event_outbox where published_at is null "
                "order by created_at,case_id,ordinal limit %s",
                (limit,),
            ).fetchall()
            yield [row[1] for row in rows]
            if rows:
                conn.execute(
                    "update event_outbox set published_at=clock_timestamp() "
                    "where event_id = any(%s)",
                    ([row[0] for row in rows],),
                )

    def prune_published(self, older_than_hours: float) -> int:
        with self._connect() as conn:
            return conn.execute(
                "delete from event_outbox where published_at "
                "< clock_timestamp() - make_interval(secs => %s)",
                (older_than_hours * 3600,),
            ).rowcount
