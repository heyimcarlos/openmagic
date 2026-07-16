"""Canonical Thread and Message transaction records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row


@dataclass(frozen=True)
class ThreadRecord:
    thread_id: UUID
    channel_kind: str
    channel_reference: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ThreadRecord:
        return cls(
            thread_id=UUID(str(record["thread_id"])),
            channel_kind=str(record["channel_kind"]),
            channel_reference=str(record["channel_reference"]),
        )


@dataclass(frozen=True)
class MessageRecord:
    message_id: UUID
    sequence: int
    author_kind: str
    author_id: str
    content: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> MessageRecord:
        return cls(
            message_id=UUID(str(record["message_id"])),
            sequence=int(record["sequence"]),
            author_kind=str(record.get("author_kind", "")),
            author_id=str(record.get("author_id", "")),
            content=str(record["content"]),
        )


def create_thread(database_url: str, record: ThreadRecord) -> None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            "INSERT INTO openmagic_runtime.threads "
            "(thread_id, channel_kind, channel_reference) VALUES (%s, %s, %s)",
            (record.thread_id, record.channel_kind, record.channel_reference),
        )


def read_thread(
    database_url: str, thread_id: UUID
) -> tuple[ThreadRecord, tuple[MessageRecord, ...]] | None:
    with (
        psycopg.connect(database_url) as connection,
        connection.transaction(),
        connection.cursor(row_factory=dict_row) as cursor,
    ):
        cursor.execute("SET TRANSACTION READ ONLY")
        thread = cursor.execute(
            "SELECT thread_id, channel_kind, channel_reference FROM openmagic_runtime.threads "
            "WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()
        if thread is None:
            return None
        messages = cursor.execute(
            "SELECT message_id, sequence, content FROM openmagic_runtime.messages "
            "WHERE thread_id = %s ORDER BY sequence",
            (thread_id,),
        ).fetchall()
    return ThreadRecord.decode(thread), tuple(MessageRecord.decode(row) for row in messages)


def append_message(
    database_url: str,
    *,
    thread_id: UUID,
    author_kind: str,
    author_id: str,
    source_kind: str,
    source_id: UUID,
    content: str,
) -> MessageRecord | None:
    message_id = uuid4()
    with (
        psycopg.connect(database_url) as connection,
        connection.transaction(),
        connection.cursor(row_factory=dict_row) as cursor,
    ):
        thread = cursor.execute(
            "SELECT thread_id FROM openmagic_runtime.threads WHERE thread_id = %s FOR UPDATE",
            (thread_id,),
        ).fetchone()
        if thread is None:
            return None
        sequence = cursor.execute(
            "SELECT COALESCE(max(sequence), 0) + 1 AS sequence "
            "FROM openmagic_runtime.messages WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()
        if sequence is None:
            raise RuntimeError("Thread message sequence could not be allocated")
        cursor.execute(
            "INSERT INTO openmagic_runtime.messages "
            "(message_id, thread_id, sequence, author_kind, author_id, source_kind, "
            "source_id, content) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                message_id,
                thread_id,
                sequence["sequence"],
                author_kind,
                author_id,
                source_kind,
                source_id,
                content,
            ),
        )
    return MessageRecord(message_id, int(sequence["sequence"]), author_kind, author_id, content)


class ThreadTransactionRecords:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def provision(self, expected: ThreadRecord) -> ThreadRecord | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            lock_keys = cursor.execute(
                "SELECT hashtextextended(%s, 1) AS identity_lock, "
                "hashtextextended(%s, 2) AS channel_lock",
                (
                    str(expected.thread_id),
                    f"{expected.channel_kind}:{expected.channel_reference}",
                ),
            ).fetchone()
            if lock_keys is None:
                raise RuntimeError("Thread provision locks could not be derived")
            for lock_key in sorted(
                (int(lock_keys["identity_lock"]), int(lock_keys["channel_lock"]))
            ):
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
            cursor.execute(
                "INSERT INTO openmagic_runtime.threads "
                "(thread_id, channel_kind, channel_reference) VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (expected.thread_id, expected.channel_kind, expected.channel_reference),
            )
            record = cursor.execute(
                "SELECT thread_id, channel_kind, channel_reference "
                "FROM openmagic_runtime.threads WHERE thread_id = %s FOR UPDATE",
                (expected.thread_id,),
            ).fetchone()
        return None if record is None else ThreadRecord.decode(record)

    def exists(self, thread_id: UUID) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM openmagic_runtime.threads WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()
            is not None
        )

    def context_cutoff(self, thread_id: UUID) -> int | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT COALESCE(max(sequence), 0) AS cutoff "
                "FROM openmagic_runtime.messages WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()
        return None if record is None else int(record["cutoff"])

    def context_messages(self, thread_id: UUID, through_sequence: int) -> tuple[MessageRecord, ...]:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            records = cursor.execute(
                "SELECT message_id, sequence, author_kind, author_id, content "
                "FROM openmagic_runtime.messages WHERE thread_id = %s AND sequence <= %s "
                "ORDER BY sequence",
                (thread_id, through_sequence),
            ).fetchall()
        return tuple(MessageRecord.decode(record) for record in records)


__all__ = [
    "MessageRecord",
    "ThreadRecord",
    "ThreadTransactionRecords",
    "append_message",
    "create_thread",
    "read_thread",
]
