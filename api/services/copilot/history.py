from abc import ABC, abstractmethod
from collections import defaultdict
from copy import deepcopy
from threading import RLock


class ChatHistory(ABC):
    @abstractmethod
    def append(self, case_id: str, turn: dict) -> None: ...

    @abstractmethod
    def recent(self, case_id: str, limit: int) -> list[dict]: ...


class MemoryChatHistory(ChatHistory):
    def __init__(self) -> None:
        self._turns: dict[str, list[dict]] = defaultdict(list)
        self._lock = RLock()

    def append(self, case_id: str, turn: dict) -> None:
        with self._lock:
            self._turns[case_id].append(deepcopy(turn))

    def recent(self, case_id: str, limit: int) -> list[dict]:
        with self._lock:
            return deepcopy(self._turns[case_id][-limit:])


class PostgresChatHistory(ChatHistory):
    def __init__(self, database_url: str):
        self.database_url = database_url

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def append(self, case_id: str, turn: dict) -> None:
        from psycopg.types.json import Jsonb

        with self._connect() as conn:
            conn.execute(
                "insert into copilot_turns (id,case_id,payload) values (%s,%s,%s)",
                (turn["turn_id"], case_id, Jsonb(turn)),
            )

    def recent(self, case_id: str, limit: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "select payload from (select payload,seq from copilot_turns where case_id=%s "
                "order by seq desc limit %s) recent order by seq",
                (case_id, limit),
            ).fetchall()
        return [row[0] for row in rows]
