"""Canonical transaction records for Command idempotency."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class StoredCommand:
    command_type: str
    schema_version: int
    command_digest: str
    result: dict[str, Any]
    result_digest: str
    committed_at: datetime

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> StoredCommand:
        return cls(
            command_type=str(record["command_type"]),
            schema_version=int(record["schema_version"]),
            command_digest=str(record["command_digest"]),
            result=dict(record["result"]),
            result_digest=str(record["result_digest"]),
            committed_at=record["committed_at"],
        )


@dataclass(frozen=True)
class StoredCommittedResult:
    command_id: UUID
    command_type: str
    schema_version: int
    result: dict[str, Any]
    result_digest: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> StoredCommittedResult:
        return cls(
            command_id=UUID(str(record["command_id"])),
            command_type=str(record["command_type"]),
            schema_version=int(record["schema_version"]),
            result=dict(record["result"]),
            result_digest=str(record["result_digest"]),
        )


def read_committed_result(
    connection: Connection[tuple[Any, ...]], command_id: UUID
) -> StoredCommittedResult | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT command_id, command_type, schema_version, result, result_digest "
            "FROM openmagic_runtime.command_receipts WHERE command_id = %s",
            (command_id,),
        ).fetchone()
    return None if record is None else StoredCommittedResult.decode(record)


def lock_command(connection: Connection[tuple[Any, ...]], command_id: UUID) -> StoredCommand | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (str(command_id),),
        )
        record = cursor.execute(
            "SELECT command_type, schema_version, command_digest, result, result_digest, "
            "committed_at FROM openmagic_runtime.command_receipts WHERE command_id = %s "
            "FOR UPDATE",
            (command_id,),
        ).fetchone()
    return None if record is None else StoredCommand.decode(record)


def insert_command(
    connection: Connection[tuple[Any, ...]],
    *,
    command_id: UUID,
    command_type: str,
    schema_version: int,
    command_digest: str,
    result: dict[str, Any],
    result_digest: str,
) -> datetime:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "INSERT INTO openmagic_runtime.command_receipts "
            "(command_id, command_type, schema_version, command_digest, result, result_digest) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING committed_at",
            (
                command_id,
                command_type,
                schema_version,
                command_digest,
                Jsonb(result),
                result_digest,
            ),
        ).fetchone()
    if record is None:
        raise RuntimeError("PostgreSQL did not return a Command commit timestamp")
    return record["committed_at"]


__all__ = [
    "StoredCommand",
    "StoredCommittedResult",
    "insert_command",
    "lock_command",
    "read_committed_result",
]
