"""Typed read-side persistence for durable Delivery state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime._delivery_contracts import (
    DeliveryAttemptState,
    DeliveryStatus,
    delivery_attempt_state,
    delivery_status,
)
from openmagic_runtime._persistence.durable_values import (
    integer_value,
    nonempty_string,
    string_value,
    timestamp_value,
    uuid_value,
)


@dataclass(frozen=True)
class DeliveredMessage:
    message_id: UUID
    thread_id: UUID
    sequence: int
    content: str
    source_kind: str
    source_id: UUID

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DeliveredMessage:
        return cls(
            message_id=uuid_value(record["message_id"]),
            thread_id=uuid_value(record["thread_id"]),
            sequence=integer_value(record["sequence"]),
            content=string_value(record["content"]),
            source_kind=nonempty_string(record["source_kind"]),
            source_id=uuid_value(record["source_id"]),
        )


@dataclass(frozen=True)
class DeliveryPresentation:
    delivery_id: UUID
    domain_event_id: UUID
    thread_id: UUID
    status: DeliveryStatus
    acknowledged: bool
    delivered_message_id: UUID | None
    message: DeliveredMessage | None


@dataclass(frozen=True)
class RuntimeDeliveryAttemptEvidence:
    delivery_attempt_id: UUID
    worker_id: str
    state: DeliveryAttemptState

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeDeliveryAttemptEvidence:
        return cls(
            delivery_attempt_id=uuid_value(record["delivery_attempt_id"]),
            worker_id=nonempty_string(record["worker_id"]),
            state=delivery_attempt_state(record["state"]),
        )


@dataclass(frozen=True)
class RuntimeDeliveryEvidence:
    delivery_id: UUID
    domain_event_id: UUID
    thread_id: UUID
    status: DeliveryStatus
    successful_attempt_id: UUID | None
    delivered_message_id: UUID | None
    attempts: tuple[RuntimeDeliveryAttemptEvidence, ...]

    def __post_init__(self) -> None:
        attempt_ids = tuple(item.delivery_attempt_id for item in self.attempts)
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("Runtime Delivery evidence contains duplicate Attempt identities")
        if self.successful_attempt_id is not None and self.successful_attempt_id not in attempt_ids:
            raise ValueError("Runtime Delivery success references an unrelated Attempt")


def _optional_uuid(value: object) -> UUID | None:
    return None if value is None else uuid_value(value)


def _decode_delivery(
    record: Mapping[str, Any],
    attempts: tuple[RuntimeDeliveryAttemptEvidence, ...],
) -> RuntimeDeliveryEvidence:
    return RuntimeDeliveryEvidence(
        delivery_id=uuid_value(record["delivery_id"]),
        domain_event_id=uuid_value(record["domain_event_id"]),
        thread_id=uuid_value(record["thread_id"]),
        status=delivery_status(record["status"]),
        successful_attempt_id=_optional_uuid(record["successful_attempt_id"]),
        delivered_message_id=_optional_uuid(record["delivered_message_id"]),
        attempts=attempts,
    )


def _delivery_attempts(
    connection: Connection[tuple[Any, ...]], delivery_id: UUID
) -> tuple[RuntimeDeliveryAttemptEvidence, ...]:
    with connection.cursor(row_factory=dict_row) as cursor:
        records = cursor.execute(
            "SELECT delivery_attempt_id, worker_id, state "
            "FROM openmagic_runtime.delivery_attempts WHERE delivery_id = %s "
            "ORDER BY created_at, delivery_attempt_id",
            (delivery_id,),
        ).fetchall()
    return tuple(RuntimeDeliveryAttemptEvidence.decode(record) for record in records)


def deliveries_for_domain_event(
    connection: Connection[tuple[Any, ...]], domain_event_id: UUID
) -> tuple[RuntimeDeliveryEvidence, ...]:
    with connection.cursor(row_factory=dict_row) as cursor:
        records = cursor.execute(
            "SELECT delivery_id, domain_event_id, thread_id, status, successful_attempt_id, "
            "delivered_message_id FROM openmagic_runtime.deliveries "
            "WHERE domain_event_id = %s ORDER BY created_at, delivery_id",
            (domain_event_id,),
        ).fetchall()
    return tuple(
        _decode_delivery(
            record,
            _delivery_attempts(connection, uuid_value(record["delivery_id"])),
        )
        for record in records
    )


def delivery_evidence(
    connection: Connection[tuple[Any, ...]],
    delivery_id: UUID,
) -> RuntimeDeliveryEvidence:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT delivery_id, domain_event_id, thread_id, status, successful_attempt_id, "
            "delivered_message_id FROM openmagic_runtime.deliveries WHERE delivery_id = %s",
            (delivery_id,),
        ).fetchone()
    if record is None:
        raise KeyError(f"Runtime Delivery not found: {delivery_id}")
    return _decode_delivery(record, _delivery_attempts(connection, delivery_id))


def _delivery_presentation(
    connection: Connection[tuple[Any, ...]],
    *,
    domain_event_id: UUID,
    thread_id: UUID,
    lock: bool,
) -> DeliveryPresentation | None:
    query = (
        "SELECT delivery_id, status, acknowledged_at, delivered_message_id "
        "FROM openmagic_runtime.deliveries WHERE domain_event_id = %s AND thread_id = %s "
        "ORDER BY created_at, delivery_id LIMIT 1"
    )
    if lock:
        query += " FOR UPDATE"
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(query, (domain_event_id, thread_id)).fetchone()
    if record is None:
        return None
    delivered_message = record["delivered_message_id"]
    delivered_message_id = uuid_value(delivered_message) if delivered_message is not None else None
    message: DeliveredMessage | None = None
    if delivered_message_id is not None:
        message_query = (
            "SELECT message_id, thread_id, sequence, content, source_kind, source_id "
            "FROM openmagic_runtime.messages WHERE message_id = %s"
        )
        if lock:
            message_query += " FOR UPDATE"
        with connection.cursor(row_factory=dict_row) as cursor:
            message_record = cursor.execute(message_query, (delivered_message_id,)).fetchone()
        if message_record is not None:
            message = DeliveredMessage.decode(message_record)
    acknowledged_at = record["acknowledged_at"]
    if acknowledged_at is not None:
        timestamp_value(acknowledged_at)
    return DeliveryPresentation(
        delivery_id=uuid_value(record["delivery_id"]),
        domain_event_id=domain_event_id,
        thread_id=thread_id,
        status=delivery_status(record["status"]),
        acknowledged=record["acknowledged_at"] is not None,
        delivered_message_id=delivered_message_id,
        message=message,
    )


def read_delivery_presentation(
    connection: Connection[tuple[Any, ...]],
    *,
    domain_event_id: UUID,
    thread_id: UUID,
) -> DeliveryPresentation | None:
    return _delivery_presentation(
        connection,
        domain_event_id=domain_event_id,
        thread_id=thread_id,
        lock=False,
    )


def lock_delivery_presentation(
    connection: Connection[tuple[Any, ...]],
    *,
    domain_event_id: UUID,
    thread_id: UUID,
) -> DeliveryPresentation | None:
    return _delivery_presentation(
        connection,
        domain_event_id=domain_event_id,
        thread_id=thread_id,
        lock=True,
    )
