"""Exact conversation Thread creation and inspection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row


@dataclass(frozen=True)
class CreateThread:
    thread_id: UUID
    channel_kind: str
    channel_reference: str


@dataclass(frozen=True)
class ThreadView:
    thread_id: UUID
    channel_kind: str
    channel_reference: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ThreadView:
        return cls(
            thread_id=UUID(str(record["thread_id"])),
            channel_kind=str(record["channel_kind"]),
            channel_reference=str(record["channel_reference"]),
        )


@dataclass(frozen=True)
class AppendMessage:
    thread_id: UUID
    author_kind: str
    author_id: str
    source_kind: str
    source_id: UUID
    content: str


@dataclass(frozen=True)
class MessageView:
    message_id: UUID
    sequence: int
    content: str


@dataclass(frozen=True)
class ThreadSnapshot:
    thread_id: UUID
    channel_kind: str
    channel_reference: str
    messages: tuple[MessageView, ...]


@dataclass(frozen=True)
class ThreadContextMessage:
    message_id: UUID
    sequence: int
    author_kind: str
    author_id: str
    content: str


@dataclass(frozen=True)
class ThreadContext:
    thread_id: UUID
    through_sequence: int
    messages: tuple[ThreadContextMessage, ...]


class ThreadStore:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url

    def create(self, request: CreateThread) -> ThreadView:
        if not request.channel_kind or not request.channel_reference:
            raise ValueError("Channel Reference must be non-empty")
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute(
                "INSERT INTO openmagic_runtime.threads "
                "(thread_id, channel_kind, channel_reference) VALUES (%s, %s, %s)",
                (request.thread_id, request.channel_kind, request.channel_reference),
            )
        return ThreadView(
            thread_id=request.thread_id,
            channel_kind=request.channel_kind,
            channel_reference=request.channel_reference,
        )

    def read(self, thread_id: UUID) -> ThreadSnapshot:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            thread = connection.execute(
                "SELECT channel_kind, channel_reference FROM openmagic_runtime.threads "
                "WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()
            if thread is None:
                raise KeyError(f"Thread not found: {thread_id}")
            messages = connection.execute(
                "SELECT message_id, sequence, content FROM openmagic_runtime.messages "
                "WHERE thread_id = %s ORDER BY sequence",
                (thread_id,),
            ).fetchall()
        return ThreadSnapshot(
            thread_id=thread_id,
            channel_kind=str(thread[0]),
            channel_reference=str(thread[1]),
            messages=tuple(
                MessageView(UUID(str(row[0])), int(row[1]), str(row[2])) for row in messages
            ),
        )

    def append(self, request: AppendMessage) -> MessageView:
        if request.source_kind not in {"channel", "delivery", "agent_run", "system"}:
            raise ValueError("Message source kind is unknown")
        if not request.author_kind or not request.author_id or not request.content:
            raise ValueError("Message author and content must be non-empty")
        message_id = uuid4()
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            thread = connection.execute(
                "SELECT thread_id FROM openmagic_runtime.threads WHERE thread_id = %s FOR UPDATE",
                (request.thread_id,),
            ).fetchone()
            if thread is None:
                raise KeyError(f"Thread not found: {request.thread_id}")
            sequence = connection.execute(
                "SELECT COALESCE(max(sequence), 0) + 1 FROM openmagic_runtime.messages "
                "WHERE thread_id = %s",
                (request.thread_id,),
            ).fetchone()
            if sequence is None:
                raise RuntimeError("Thread message sequence could not be allocated")
            connection.execute(
                "INSERT INTO openmagic_runtime.messages "
                "(message_id, thread_id, sequence, author_kind, author_id, source_kind, "
                "source_id, content) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    message_id,
                    request.thread_id,
                    int(sequence[0]),
                    request.author_kind,
                    request.author_id,
                    request.source_kind,
                    request.source_id,
                    request.content,
                ),
            )
        return MessageView(message_id, int(sequence[0]), request.content)


class ThreadAccess:
    """Transaction-scoped exact Thread authority for application Controllers."""

    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def require(self, thread_id: UUID) -> None:
        row = self._connection.execute(
            "SELECT 1 FROM openmagic_runtime.threads WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Thread not found: {thread_id}")

    def metadata(self, thread_id: UUID) -> ThreadView:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT thread_id, channel_kind, channel_reference "
                "FROM openmagic_runtime.threads WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()
        if record is None:
            raise KeyError(f"Thread not found: {thread_id}")
        return ThreadView.decode(record)

    def context_cutoff(self, thread_id: UUID) -> int:
        self.require(thread_id)
        row = self._connection.execute(
            "SELECT COALESCE(max(sequence), 0) FROM openmagic_runtime.messages "
            "WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Thread context cutoff could not be established")
        return int(row[0])

    def context(self, thread_id: UUID, through_sequence: int) -> ThreadContext:
        if through_sequence < 0:
            raise ValueError("Thread context cutoff cannot be negative")
        self.require(thread_id)
        current = self.context_cutoff(thread_id)
        if through_sequence > current:
            raise ValueError("Thread context cutoff is beyond durable Thread history")
        rows = self._connection.execute(
            "SELECT message_id, sequence, author_kind, author_id, content "
            "FROM openmagic_runtime.messages WHERE thread_id = %s AND sequence <= %s "
            "ORDER BY sequence",
            (thread_id, through_sequence),
        ).fetchall()
        return ThreadContext(
            thread_id=thread_id,
            through_sequence=through_sequence,
            messages=tuple(
                ThreadContextMessage(
                    message_id=UUID(str(row[0])),
                    sequence=int(row[1]),
                    author_kind=str(row[2]),
                    author_id=str(row[3]),
                    content=str(row[4]),
                )
                for row in rows
            ),
        )


__all__ = [
    "AppendMessage",
    "CreateThread",
    "MessageView",
    "ThreadAccess",
    "ThreadContext",
    "ThreadContextMessage",
    "ThreadSnapshot",
    "ThreadStore",
    "ThreadView",
]
