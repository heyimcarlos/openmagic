"""Canonical creation of immutable Delivery intent."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from openmagic_runtime._delivery_contracts import (
    DeliveryIntent,
    DeliveryRetryPolicy,
)


class DeliveryIntentRecords:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def create(
        self,
        *,
        domain_event_id: UUID,
        thread_id: UUID,
        audience: dict[str, Any],
        message_author: dict[str, Any],
        content_descriptor: dict[str, Any],
        message_content: str,
        retry_policy: DeliveryRetryPolicy,
    ) -> DeliveryIntent:
        if set(message_author) != {"kind", "identifier"} or not all(
            isinstance(value, str) and value for value in message_author.values()
        ):
            raise ValueError("Message author must contain non-empty kind and identifier")
        if not audience or not content_descriptor or not message_content:
            raise ValueError("Delivery audience and content descriptor must be non-empty")
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cutoff_record = cursor.execute(
                "SELECT COALESCE(max(sequence), 0) AS cutoff "
                "FROM openmagic_runtime.messages WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()
        if cutoff_record is None:
            raise RuntimeError("Thread cutoff could not be established")
        cutoff = int(cutoff_record["cutoff"])
        delivery_id = uuid4()
        self._connection.execute(
            "INSERT INTO openmagic_runtime.deliveries "
            "(delivery_id, domain_event_id, thread_id, audience, message_author, "
            "content_mode, content_descriptor, message_content, retry_policy, "
            "context_through_sequence, status) VALUES "
            "(%s, %s, %s, %s, %s, 'template', %s, %s, %s, %s, 'pending')",
            (
                delivery_id,
                domain_event_id,
                thread_id,
                Jsonb(audience),
                Jsonb(message_author),
                Jsonb(content_descriptor),
                message_content,
                Jsonb(
                    {
                        "version": retry_policy.version,
                        "max_attempts": retry_policy.max_attempts,
                        "delays_seconds": list(retry_policy.delays_seconds),
                        "lease_seconds": retry_policy.lease_seconds,
                        "retryable_failure_classes": list(retry_policy.retryable_failure_classes),
                        "terminal_failure_classes": list(retry_policy.terminal_failure_classes),
                    }
                ),
                cutoff,
            ),
        )
        return DeliveryIntent(delivery_id, domain_event_id, thread_id, cutoff)


__all__ = ["DeliveryIntentRecords"]
