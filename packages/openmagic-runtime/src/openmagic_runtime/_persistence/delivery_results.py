"""Delivery acknowledgement and failure result persistence."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime._delivery_contracts import (
    ClaimedDelivery,
    DeliveryAcknowledgement,
    DeliveryFailureDisposition,
    DeliveryProposalConflict,
    StaleDeliveryAuthority,
)
from openmagic_runtime._persistence.delivery_work_models import (
    DeliveryAcknowledgementRecord,
    DeliveryAttemptAuthorityRecord,
    DeliveryFailureAttemptRecord,
    DeliveryFailureContextRecord,
    DeliveryRecord,
)


class DeliveryResultRecords:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def acknowledge(
        self,
        claim: ClaimedDelivery,
        *,
        worker_id: str,
        proposed_thread_id: UUID,
    ) -> DeliveryAcknowledgement:
        delivery = self._lock_delivery(claim.delivery_id)
        if delivery.status == "delivered":
            if delivery.successful_attempt_id != claim.delivery_attempt_id:
                raise StaleDeliveryAuthority("Delivery authority is stale")
            return self.acknowledgement(claim.delivery_attempt_id)
        if proposed_thread_id != delivery.thread_id or proposed_thread_id != claim.thread_id:
            raise DeliveryProposalConflict("Delivery proposal targets the wrong exact Thread")
        authority = self._lock_attempt(claim)
        if (
            authority.state != "running"
            or authority.worker_id != worker_id
            or not authority.lease_valid
        ):
            raise StaleDeliveryAuthority("Delivery Attempt authority is stale")

        self._connection.execute(
            "SELECT thread_id FROM openmagic_runtime.threads WHERE thread_id = %s FOR UPDATE",
            (delivery.thread_id,),
        ).fetchone()
        with self._connection.cursor(row_factory=dict_row) as cursor:
            sequence_record = cursor.execute(
                "SELECT COALESCE(max(sequence), 0) + 1 AS next_sequence "
                "FROM openmagic_runtime.messages WHERE thread_id = %s",
                (delivery.thread_id,),
            ).fetchone()
        if sequence_record is None:
            raise RuntimeError("Message Sequence could not be allocated")
        message_id = uuid4()
        self._connection.execute(
            "INSERT INTO openmagic_runtime.messages "
            "(message_id, thread_id, sequence, author_kind, author_id, source_kind, "
            "source_id, content) VALUES (%s, %s, %s, %s, %s, 'delivery', %s, %s)",
            (
                message_id,
                delivery.thread_id,
                int(sequence_record["next_sequence"]),
                delivery.message_author.kind,
                delivery.message_author.identifier,
                claim.delivery_id,
                delivery.message_content,
            ),
        )
        with self._connection.cursor(row_factory=dict_row) as cursor:
            acknowledged = cursor.execute(
                "UPDATE openmagic_runtime.delivery_attempts SET state = 'succeeded', "
                "completed_at = clock_timestamp() WHERE delivery_attempt_id = %s "
                "RETURNING completed_at",
                (claim.delivery_attempt_id,),
            ).fetchone()
        self._connection.execute(
            "UPDATE openmagic_runtime.deliveries SET status = 'delivered', "
            "successful_attempt_id = %s, delivered_message_id = %s, "
            "acknowledged_at = clock_timestamp() WHERE delivery_id = %s",
            (claim.delivery_attempt_id, message_id, claim.delivery_id),
        )
        if acknowledged is None:
            raise RuntimeError("Delivery acknowledgement timestamp was not returned")
        return self.acknowledgement(claim.delivery_attempt_id)

    def acknowledgement(self, delivery_attempt_id: UUID) -> DeliveryAcknowledgement:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT d.delivery_id, d.thread_id, d.delivered_message_id, m.sequence, "
                "d.acknowledged_at FROM openmagic_runtime.deliveries AS d "
                "JOIN openmagic_runtime.messages AS m "
                "ON m.message_id = d.delivered_message_id "
                "WHERE d.successful_attempt_id = %s AND d.status = 'delivered'",
                (delivery_attempt_id,),
            ).fetchone()
        if record is None:
            raise RuntimeError("Delivery Acknowledgement does not exist")
        return DeliveryAcknowledgementRecord.decode(record).acknowledgement(delivery_attempt_id)

    def report_failure(
        self,
        claim: ClaimedDelivery,
        *,
        worker_id: str,
        failure_class: str,
    ) -> DeliveryFailureDisposition:
        context = self._lock_failure_context(claim.delivery_id)
        authority = self._lock_failure_attempt(claim)
        if (
            authority.state != "running"
            or authority.worker_id != worker_id
            or not authority.lease_valid
            or authority.attempt_number != claim.attempt_number
            or context.thread_id != claim.thread_id
            or context.status != "pending"
        ):
            raise StaleDeliveryAuthority("Delivery Attempt authority is stale")
        retryable = context.policy.retryable_failure_classes
        terminal = context.policy.terminal_failure_classes
        if failure_class not in retryable and failure_class not in terminal:
            raise DeliveryProposalConflict("Delivery failure class is not qualified by policy")
        self._connection.execute(
            "UPDATE openmagic_runtime.delivery_attempts SET state = 'failed', "
            "completed_at = clock_timestamp() WHERE delivery_attempt_id = %s",
            (claim.delivery_attempt_id,),
        )
        if failure_class in retryable and claim.attempt_number < context.policy.max_attempts:
            delay = context.policy.delays_seconds[claim.attempt_number - 1]
            self._connection.execute(
                "UPDATE openmagic_runtime.deliveries SET next_eligible_at = "
                "clock_timestamp() + (%s * interval '1 second') WHERE delivery_id = %s",
                (delay, claim.delivery_id),
            )
            outcome: Literal["retry_scheduled", "failed"] = "retry_scheduled"
        else:
            self._connection.execute(
                "UPDATE openmagic_runtime.deliveries SET status = 'failed' WHERE delivery_id = %s",
                (claim.delivery_id,),
            )
            outcome = "failed"
        return DeliveryFailureDisposition(
            delivery_id=claim.delivery_id,
            delivery_attempt_id=claim.delivery_attempt_id,
            outcome=outcome,
        )

    def _lock_delivery(self, delivery_id: UUID) -> DeliveryRecord:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT thread_id, status, successful_attempt_id, "
                "message_author, message_content FROM openmagic_runtime.deliveries "
                "WHERE delivery_id = %s FOR UPDATE",
                (delivery_id,),
            ).fetchone()
        if record is None:
            raise RuntimeError("Delivery not found")
        return DeliveryRecord.decode(record)

    def _lock_attempt(self, claim: ClaimedDelivery) -> DeliveryAttemptAuthorityRecord:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT state, worker_id, "
                "lease_expires_at > clock_timestamp() AS lease_valid "
                "FROM openmagic_runtime.delivery_attempts "
                "WHERE delivery_attempt_id = %s AND delivery_id = %s FOR UPDATE",
                (claim.delivery_attempt_id, claim.delivery_id),
            ).fetchone()
        if record is None:
            raise StaleDeliveryAuthority("Delivery Attempt authority is stale")
        return DeliveryAttemptAuthorityRecord.decode(record)

    def _lock_failure_context(self, delivery_id: UUID) -> DeliveryFailureContextRecord:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT thread_id, retry_policy, status "
                "FROM openmagic_runtime.deliveries WHERE delivery_id = %s FOR UPDATE",
                (delivery_id,),
            ).fetchone()
        if record is None:
            raise RuntimeError("Delivery not found")
        return DeliveryFailureContextRecord.decode(record)

    def _lock_failure_attempt(self, claim: ClaimedDelivery) -> DeliveryFailureAttemptRecord:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT state, worker_id, "
                "lease_expires_at > clock_timestamp() AS lease_valid, attempt_number "
                "FROM openmagic_runtime.delivery_attempts "
                "WHERE delivery_attempt_id = %s AND delivery_id = %s FOR UPDATE",
                (claim.delivery_attempt_id, claim.delivery_id),
            ).fetchone()
        if record is None:
            raise StaleDeliveryAuthority("Delivery Attempt authority is stale")
        return DeliveryFailureAttemptRecord.decode(record)


def acknowledge_delivery_record(
    *,
    database_url: str,
    claim: ClaimedDelivery,
    worker_id: str,
    proposed_thread_id: UUID,
) -> DeliveryAcknowledgement:
    with psycopg.connect(database_url) as connection, connection.transaction():
        return DeliveryResultRecords(connection).acknowledge(
            claim,
            worker_id=worker_id,
            proposed_thread_id=proposed_thread_id,
        )


__all__ = ["DeliveryResultRecords", "acknowledge_delivery_record"]
