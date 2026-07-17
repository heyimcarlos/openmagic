"""Canonical PostgreSQL ownership for deterministic race overlap barriers."""

from __future__ import annotations

import time
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any, ClassVar, LiteralString, Self

import psycopg
from openmagic_runtime.processes import finish_owned_cleanup


class _RaceBarrier(AbstractContextManager["_RaceBarrier"]):
    lock_sql: ClassVar[LiteralString]
    unlock_sql: ClassVar[LiteralString]

    def __init__(self, database_url: str, barrier_key: int) -> None:
        self._database_url = database_url
        self._barrier_key = barrier_key
        self._connection: psycopg.Connection[tuple[Any, ...]] | None = None
        self._held = False

    def __enter__(self) -> Self:
        self._connection = psycopg.connect(self._database_url, autocommit=True)
        return self

    @property
    def connection(self) -> psycopg.Connection[tuple[Any, ...]]:
        if self._connection is None:
            raise RuntimeError("race barrier is not connected")
        return self._connection

    def acquire(self) -> None:
        self.connection.execute(self.lock_sql, (self._barrier_key,))
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        self.connection.execute(self.unlock_sql, (self._barrier_key,))
        self._held = False

    def close(self) -> None:
        errors: list[BaseException] = []
        try:
            self.release()
        except BaseException as error:
            errors.append(error)
        if self._connection is not None:
            try:
                self._connection.close()
            except BaseException as error:
                errors.append(error)
            self._connection = None
        if errors:
            raise BaseExceptionGroup("race barrier cleanup failed", errors)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        finish_owned_cleanup(
            self.close,
            execution_error=exc_value,
            message="race barrier execution and cleanup failed",
        )
        return None


class RaceContenderBarrier(_RaceBarrier):
    lock_sql = "SELECT pg_advisory_lock_shared(%s)"
    unlock_sql = "SELECT pg_advisory_unlock_shared(%s)"

    @property
    def backend_id(self) -> int:
        return self.connection.info.backend_pid


class RaceCoordinatorBarrier(_RaceBarrier):
    lock_sql = "SELECT pg_advisory_lock(%s)"
    unlock_sql = "SELECT pg_advisory_unlock(%s)"

    def __enter__(self) -> RaceCoordinatorBarrier:
        super().__enter__()
        try:
            self.acquire()
        except BaseException as error:
            finish_owned_cleanup(
                self.close,
                execution_error=error,
                message="race coordinator acquisition and cleanup failed",
            )
            raise
        return self

    def await_waiters(self, backend_ids: tuple[int, ...]) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            row = self.connection.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE pid = ANY(%s) AND wait_event_type = 'Lock' "
                "AND wait_event = 'advisory'",
                (list(backend_ids),),
            ).fetchone()
            if row == (2,):
                return
            time.sleep(0.01)
        observed = self.connection.execute(
            "SELECT pid, state, wait_event_type, wait_event FROM pg_stat_activity "
            "WHERE pid = ANY(%s) ORDER BY pid",
            (list(backend_ids),),
        ).fetchall()
        raise TimeoutError(f"PostgreSQL did not observe both race contenders waiting: {observed!r}")

    def require_overlap(self, backend_ids: tuple[int, ...]) -> None:
        row = self.connection.execute(
            "SELECT count(*) FROM pg_locks WHERE pid = ANY(%s) "
            "AND locktype = 'advisory' AND granted",
            (list(backend_ids),),
        ).fetchone()
        if row != (2,):
            raise AssertionError("PostgreSQL did not grant both shared overlap locks")


__all__ = ["RaceContenderBarrier", "RaceCoordinatorBarrier"]
