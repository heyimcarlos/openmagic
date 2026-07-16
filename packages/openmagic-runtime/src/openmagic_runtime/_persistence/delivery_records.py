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
    invalid_durable_value,
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
class RuntimeDeliveryEvidence:
    delivery_id: UUID
    status: DeliveryStatus
    delivered_message_id: UUID | None
    attempt_states: tuple[DeliveryAttemptState, ...]

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeDeliveryEvidence:
        delivered_message = record["delivered_message_id"]
        return cls(
            delivery_id=uuid_value(record["delivery_id"]),
            status=delivery_status(record["status"]),
            delivered_message_id=(
                uuid_value(delivered_message) if delivered_message is not None else None
            ),
            attempt_states=_attempt_states(record["attempt_states"]),
        )


def _attempt_states(value: object) -> tuple[DeliveryAttemptState, ...]:
    if not isinstance(value, list):
        raise invalid_durable_value()
    return tuple(delivery_attempt_state(item) for item in value)


def deliveries_for_domain_event(
    connection: Connection[tuple[Any, ...]], domain_event_id: UUID
) -> tuple[RuntimeDeliveryEvidence, ...]:
    with connection.cursor(row_factory=dict_row) as cursor:
        records = cursor.execute(
            "SELECT d.delivery_id, d.status, d.delivered_message_id, "
            "COALESCE(array_agg(a.state ORDER BY a.created_at, a.delivery_attempt_id) "
            "FILTER (WHERE a.delivery_attempt_id IS NOT NULL), ARRAY[]::text[]) "
            "AS attempt_states FROM openmagic_runtime.deliveries d "
            "LEFT JOIN openmagic_runtime.delivery_attempts a "
            "ON a.delivery_id = d.delivery_id WHERE d.domain_event_id = %s "
            "GROUP BY d.delivery_id ORDER BY d.created_at, d.delivery_id",
            (domain_event_id,),
        ).fetchall()
    return tuple(RuntimeDeliveryEvidence.decode(record) for record in records)


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
