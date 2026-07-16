"""Canonical Agent Run transaction records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class AgentRunRecord:
    agent_run_id: UUID
    agent_key: str
    thread_id: UUID
    context_through_sequence: int
    status: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> AgentRunRecord:
        return cls(
            agent_run_id=UUID(str(record["agent_run_id"])),
            agent_key=str(record["agent_key"]),
            thread_id=UUID(str(record["thread_id"])),
            context_through_sequence=int(record["context_through_sequence"]),
            status=str(record["status"]),
        )


def insert_agent_run(
    connection: Connection[tuple[Any, ...]],
    *,
    agent_run_id: UUID,
    attempt_id: UUID,
    agent_key: str,
    thread_id: UUID,
    context_through_sequence: int,
    input_value: dict[str, object],
) -> None:
    connection.execute(
        "INSERT INTO openmagic_runtime.agent_runs "
        "(agent_run_id, attempt_id, agent_key, thread_id, context_through_sequence, input, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'running')",
        (
            agent_run_id,
            attempt_id,
            agent_key,
            thread_id,
            context_through_sequence,
            Jsonb(input_value),
        ),
    )


def read_running_input(
    connection: Connection[tuple[Any, ...]], attempt_id: UUID
) -> tuple[UUID, dict[str, Any]] | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT agent_run_id, input FROM openmagic_runtime.agent_runs "
            "WHERE attempt_id = %s AND status = 'running'",
            (attempt_id,),
        ).fetchone()
    if record is None:
        return None
    return UUID(str(record["agent_run_id"])), dict(record["input"])


def complete_agent_run(
    connection: Connection[tuple[Any, ...]],
    *,
    agent_run_id: UUID,
    attempt_id: UUID,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    updated = connection.execute(
        "UPDATE openmagic_runtime.agent_runs SET status = 'completed', result = %s, "
        "completed_at = clock_timestamp() WHERE agent_run_id = %s AND attempt_id = %s "
        "AND status = 'running' RETURNING agent_run_id",
        (Jsonb(result), agent_run_id, attempt_id),
    ).fetchone()
    if updated is not None:
        return result
    with connection.cursor(row_factory=dict_row) as cursor:
        existing = cursor.execute(
            "SELECT result FROM openmagic_runtime.agent_runs "
            "WHERE attempt_id = %s AND status = 'completed'",
            (attempt_id,),
        ).fetchone()
    return None if existing is None else dict(existing["result"])


def finish_agent_run(
    connection: Connection[tuple[Any, ...]],
    *,
    attempt_id: UUID,
    status: str,
    result: dict[str, Any],
) -> None:
    connection.execute(
        "UPDATE openmagic_runtime.agent_runs SET status = %s, result = %s, "
        "completed_at = clock_timestamp() WHERE attempt_id = %s AND status = 'running'",
        (status, Jsonb(result), attempt_id),
    )


def find_agent_run(
    connection: Connection[tuple[Any, ...]], attempt_id: UUID
) -> AgentRunRecord | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT agent_run_id, agent_key, thread_id, context_through_sequence, status "
            "FROM openmagic_runtime.agent_runs WHERE attempt_id = %s",
            (attempt_id,),
        ).fetchone()
    return None if record is None else AgentRunRecord.decode(record)


__all__ = [
    "AgentRunRecord",
    "complete_agent_run",
    "find_agent_run",
    "finish_agent_run",
    "insert_agent_run",
    "read_running_input",
]
