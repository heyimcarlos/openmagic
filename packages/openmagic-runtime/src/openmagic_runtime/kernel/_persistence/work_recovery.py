"""Expired Attempt recovery persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime.kernel._persistence.trace import append_trace
from openmagic_runtime.kernel._work_contracts import DispositionRequired


@dataclass(frozen=True)
class _ExpiredAttempt:
    attempt_id: UUID
    step_id: UUID
    attempt_number: int
    template_key: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> _ExpiredAttempt:
        return cls(
            attempt_id=UUID(str(record["attempt_id"])),
            step_id=UUID(str(record["step_id"])),
            attempt_number=int(record["attempt_number"]),
            template_key=str(record["template_key"]),
        )


class AttemptRecoveryRecords:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def recover_expired(self, instance_id: UUID | None = None) -> DispositionRequired | None:
        locked_instance_id = self._lock_expired_instance(instance_id)
        if locked_instance_id is None:
            return None
        expired = self._lock_expired_attempt(locked_instance_id)
        if expired is None:
            return None
        self._connection.execute(
            "UPDATE openmagic_runtime.attempts SET state = 'abandoned', "
            "completed_at = clock_timestamp() WHERE attempt_id = %s",
            (expired.attempt_id,),
        )
        append_trace(
            self._connection,
            instance_id=locked_instance_id,
            event_type="attempt_abandoned",
            source_kind="attempt_abandonment",
            source_id=expired.attempt_id,
            input_value={"attempt_id": str(expired.attempt_id)},
            receipt=lambda _: {
                "attempt_id": str(expired.attempt_id),
                "step_id": str(expired.step_id),
            },
        )
        return DispositionRequired(
            instance_id=locked_instance_id,
            step_id=expired.step_id,
            attempt_id=expired.attempt_id,
            attempt_number=expired.attempt_number,
            template_key=expired.template_key,
            observation={"expiry_cause": "lease_or_hard_deadline"},
            basis_state="abandoned",
        )

    def _lock_expired_instance(self, instance_id: UUID | None) -> UUID | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT i.instance_id FROM openmagic_runtime.instances AS i "
                "WHERE i.state = 'open' AND (%s::uuid IS NULL OR i.instance_id = %s) "
                "AND EXISTS (SELECT 1 FROM openmagic_runtime.attempts AS a "
                "WHERE a.instance_id = i.instance_id AND a.state = 'leased' "
                "AND (a.lease_expires_at <= clock_timestamp() "
                "OR a.hard_deadline <= clock_timestamp())) "
                "ORDER BY i.created_at, i.instance_id FOR UPDATE SKIP LOCKED LIMIT 1",
                (instance_id, instance_id),
            ).fetchone()
        return None if record is None else UUID(str(record["instance_id"]))

    def _lock_expired_attempt(self, instance_id: UUID) -> _ExpiredAttempt | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT a.attempt_id, a.step_id, a.attempt_number, s.template_key "
                "FROM openmagic_runtime.attempts AS a "
                "JOIN openmagic_runtime.steps AS s ON s.step_id = a.step_id "
                "WHERE a.instance_id = %s AND a.state = 'leased' "
                "AND (a.lease_expires_at <= clock_timestamp() "
                "OR a.hard_deadline <= clock_timestamp()) "
                "ORDER BY a.created_at, a.attempt_id FOR UPDATE OF a LIMIT 1",
                (instance_id,),
            ).fetchone()
        return None if record is None else _ExpiredAttempt.decode(record)


__all__ = ["AttemptRecoveryRecords"]
