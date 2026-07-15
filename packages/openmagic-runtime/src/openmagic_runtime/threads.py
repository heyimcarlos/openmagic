"""Exact conversation Thread creation and inspection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection


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


__all__ = [
    "CreateThread",
    "MessageView",
    "ThreadAccess",
    "ThreadSnapshot",
    "ThreadStore",
    "ThreadView",
]
