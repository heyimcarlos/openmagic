"""Exact conversation Thread creation and inspection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection

from openmagic_runtime._persistence.thread_records import (
    ThreadRecord,
    ThreadTransactionRecords,
    append_message,
    create_thread,
    read_thread,
)


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
        create_thread(
            self._database_url,
            ThreadRecord(request.thread_id, request.channel_kind, request.channel_reference),
        )
        return ThreadView(
            thread_id=request.thread_id,
            channel_kind=request.channel_kind,
            channel_reference=request.channel_reference,
        )

    def read(self, thread_id: UUID) -> ThreadSnapshot:
        snapshot = read_thread(self._database_url, thread_id)
        if snapshot is None:
            raise KeyError(f"Thread not found: {thread_id}")
        thread, messages = snapshot
        return ThreadSnapshot(
            thread_id=thread_id,
            channel_kind=thread.channel_kind,
            channel_reference=thread.channel_reference,
            messages=tuple(
                MessageView(row.message_id, row.sequence, row.content) for row in messages
            ),
        )

    def append(self, request: AppendMessage) -> MessageView:
        if request.source_kind not in {"channel", "delivery", "agent_run", "system"}:
            raise ValueError("Message source kind is unknown")
        if not request.author_kind or not request.author_id or not request.content:
            raise ValueError("Message author and content must be non-empty")
        message = append_message(
            self._database_url,
            thread_id=request.thread_id,
            author_kind=request.author_kind,
            author_id=request.author_id,
            source_kind=request.source_kind,
            source_id=request.source_id,
            content=request.content,
        )
        if message is None:
            raise KeyError(f"Thread not found: {request.thread_id}")
        return MessageView(message.message_id, message.sequence, message.content)


class ThreadAccess:
    """Transaction-scoped exact Thread authority for application Controllers."""

    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._records = ThreadTransactionRecords(connection)

    def provision(self, request: CreateThread) -> ThreadView:
        """Create or verify one exact Thread in the caller-owned transaction."""
        if not request.channel_kind or not request.channel_reference:
            raise ValueError("Channel Reference must be non-empty")
        record = self._records.provision(
            ThreadRecord(request.thread_id, request.channel_kind, request.channel_reference)
        )
        if record is None:
            raise ValueError("Channel Reference was already provisioned to another Thread")
        durable = ThreadView(record.thread_id, record.channel_kind, record.channel_reference)
        expected = ThreadView(
            request.thread_id,
            request.channel_kind,
            request.channel_reference,
        )
        if durable != expected:
            raise ValueError("Thread ID was already provisioned with another Channel Reference")
        return durable

    def require(self, thread_id: UUID) -> None:
        if not self._records.exists(thread_id):
            raise KeyError(f"Thread not found: {thread_id}")

    def context_cutoff(self, thread_id: UUID) -> int:
        self.require(thread_id)
        cutoff = self._records.context_cutoff(thread_id)
        if cutoff is None:
            raise RuntimeError("Thread context cutoff could not be established")
        return cutoff

    def context(self, thread_id: UUID, through_sequence: int) -> ThreadContext:
        if through_sequence < 0:
            raise ValueError("Thread context cutoff cannot be negative")
        self.require(thread_id)
        current = self.context_cutoff(thread_id)
        if through_sequence > current:
            raise ValueError("Thread context cutoff is beyond durable Thread history")
        rows = self._records.context_messages(thread_id, through_sequence)
        return ThreadContext(
            thread_id=thread_id,
            through_sequence=through_sequence,
            messages=tuple(
                ThreadContextMessage(
                    message_id=row.message_id,
                    sequence=row.sequence,
                    author_kind=row.author_kind,
                    author_id=row.author_id,
                    content=row.content,
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
