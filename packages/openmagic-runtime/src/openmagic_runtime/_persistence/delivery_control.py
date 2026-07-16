"""Canonical persistence owner for exact-Thread Delivery."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._delivery_contracts import (
    ClaimDelivery,
    ClaimedDelivery,
    DeliveryAcknowledgement,
    DeliveryFailureDisposition,
    DeliveryIntent,
    DeliveryProposalConflict,
    DeliveryRetryPolicy,
    StaleDeliveryAuthority,
)


class DeliveryControlTransaction:
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
        cutoff = self._connection.execute(
            "SELECT COALESCE(max(sequence), 0) FROM openmagic_runtime.messages WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()
        if cutoff is None:
            raise RuntimeError("Thread cutoff could not be established")
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
                int(cutoff[0]),
            ),
        )
        return DeliveryIntent(delivery_id, domain_event_id, thread_id, int(cutoff[0]))


class DeliveryWorkTransaction:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def claim(self, request: ClaimDelivery) -> ClaimedDelivery | None:
        replay = self._connection.execute(
            "SELECT delivery_attempt_id, delivery_id, attempt_number FROM "
            "openmagic_runtime.delivery_attempts WHERE claim_request_id = %s",
            (request.claim_request_id,),
        ).fetchone()
        if replay is not None:
            return self._claimed_from_ids(UUID(str(replay[0])))
        expired = self._connection.execute(
            "SELECT d.delivery_id, d.retry_policy, a.attempt_number "
            "FROM openmagic_runtime.deliveries AS d "
            "JOIN openmagic_runtime.delivery_attempts AS a ON a.delivery_id = d.delivery_id "
            "WHERE d.status = 'pending' AND EXISTS ("
            "SELECT 1 FROM openmagic_runtime.delivery_attempts AS current_attempt "
            "WHERE current_attempt.delivery_id = d.delivery_id "
            "AND current_attempt.state = 'running' "
            "AND current_attempt.lease_expires_at <= clock_timestamp()) "
            "AND a.state = 'running' AND a.lease_expires_at <= clock_timestamp() "
            "ORDER BY d.created_at, d.delivery_id FOR UPDATE SKIP LOCKED LIMIT 1"
        ).fetchone()
        if expired is not None:
            self._connection.execute(
                "UPDATE openmagic_runtime.delivery_attempts SET state = 'abandoned', "
                "completed_at = clock_timestamp() WHERE delivery_id = %s AND state = 'running' "
                "AND lease_expires_at <= clock_timestamp()",
                (expired[0],),
            )
            policy = dict(expired[1])
            attempt_number = int(expired[2])
            if attempt_number >= int(policy["max_attempts"]):
                self._connection.execute(
                    "UPDATE openmagic_runtime.deliveries SET status = 'failed' "
                    "WHERE delivery_id = %s",
                    (expired[0],),
                )
            else:
                delay = int(policy["delays_seconds"][attempt_number - 1])
                self._connection.execute(
                    "UPDATE openmagic_runtime.deliveries SET next_eligible_at = "
                    "clock_timestamp() + (%s * interval '1 second') WHERE delivery_id = %s",
                    (delay, expired[0]),
                )
        delivery = self._connection.execute(
            "SELECT d.delivery_id, d.thread_id, d.content_descriptor, "
            "d.context_through_sequence, d.retry_policy FROM openmagic_runtime.deliveries AS d "
            "WHERE d.status = 'pending' AND d.next_eligible_at <= clock_timestamp() "
            "AND NOT EXISTS (SELECT 1 FROM openmagic_runtime.delivery_attempts AS a "
            "WHERE a.delivery_id = d.delivery_id AND a.state = 'running') "
            "ORDER BY d.next_eligible_at, d.delivery_id FOR UPDATE SKIP LOCKED LIMIT 1"
        ).fetchone()
        if delivery is None:
            return None
        previous = self._connection.execute(
            "SELECT count(*) FROM openmagic_runtime.delivery_attempts WHERE delivery_id = %s",
            (delivery[0],),
        ).fetchone()
        attempt_number = int(previous[0]) + 1 if previous is not None else 1
        policy = dict(delivery[4])
        if attempt_number > int(policy["max_attempts"]):
            self._connection.execute(
                "UPDATE openmagic_runtime.deliveries SET status = 'failed' WHERE delivery_id = %s",
                (delivery[0],),
            )
            return None
        attempt_id = uuid4()
        self._connection.execute(
            "INSERT INTO openmagic_runtime.delivery_attempts "
            "(delivery_attempt_id, claim_request_id, delivery_id, attempt_number, state, "
            "worker_id, lease_expires_at) VALUES (%s, %s, %s, %s, 'running', %s, "
            "clock_timestamp() + (%s * interval '1 second'))",
            (
                attempt_id,
                request.claim_request_id,
                delivery[0],
                attempt_number,
                request.worker_id,
                int(policy["lease_seconds"]),
            ),
        )
        return ClaimedDelivery(
            delivery_attempt_id=attempt_id,
            delivery_id=UUID(str(delivery[0])),
            attempt_number=attempt_number,
            thread_id=UUID(str(delivery[1])),
            content_descriptor=dict(delivery[2]),
            context_through_sequence=int(delivery[3]),
        )

    def acknowledge(
        self,
        claim: ClaimedDelivery,
        *,
        worker_id: str,
        proposed_thread_id: UUID,
    ) -> DeliveryAcknowledgement:
        delivery = self._connection.execute(
            "SELECT thread_id, status, successful_attempt_id, delivered_message_id, "
            "acknowledged_at, message_author, message_content "
            "FROM openmagic_runtime.deliveries WHERE delivery_id = %s FOR UPDATE",
            (claim.delivery_id,),
        ).fetchone()
        if delivery is None:
            raise RuntimeError("Delivery not found")
        if delivery[1] == "delivered":
            if UUID(str(delivery[2])) != claim.delivery_attempt_id:
                raise StaleDeliveryAuthority("Delivery authority is stale")
            return self._acknowledgement(claim.delivery_attempt_id)
        durable_thread_id = UUID(str(delivery[0]))
        if proposed_thread_id != durable_thread_id or proposed_thread_id != claim.thread_id:
            raise DeliveryProposalConflict("Delivery proposal targets the wrong exact Thread")
        attempt = self._connection.execute(
            "SELECT state, worker_id, lease_expires_at > clock_timestamp() FROM "
            "openmagic_runtime.delivery_attempts WHERE delivery_attempt_id = %s "
            "AND delivery_id = %s FOR UPDATE",
            (claim.delivery_attempt_id, claim.delivery_id),
        ).fetchone()
        if attempt is None or attempt[0] != "running" or attempt[1] != worker_id or not attempt[2]:
            raise StaleDeliveryAuthority("Delivery Attempt authority is stale")
        self._connection.execute(
            "SELECT thread_id FROM openmagic_runtime.threads WHERE thread_id = %s FOR UPDATE",
            (durable_thread_id,),
        ).fetchone()
        sequence = self._connection.execute(
            "SELECT COALESCE(max(sequence), 0) + 1 FROM openmagic_runtime.messages "
            "WHERE thread_id = %s",
            (durable_thread_id,),
        ).fetchone()
        if sequence is None:
            raise RuntimeError("Message Sequence could not be allocated")
        message_id = uuid4()
        message_author = dict(delivery[5])
        message_content = str(delivery[6])
        self._connection.execute(
            "INSERT INTO openmagic_runtime.messages "
            "(message_id, thread_id, sequence, author_kind, author_id, source_kind, "
            "source_id, content) VALUES (%s, %s, %s, %s, %s, 'delivery', %s, %s)",
            (
                message_id,
                durable_thread_id,
                int(sequence[0]),
                message_author["kind"],
                message_author["identifier"],
                claim.delivery_id,
                message_content,
            ),
        )
        acknowledged = self._connection.execute(
            "UPDATE openmagic_runtime.delivery_attempts SET state = 'succeeded', "
            "completed_at = clock_timestamp() WHERE delivery_attempt_id = %s RETURNING completed_at",
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
        return self._acknowledgement(claim.delivery_attempt_id)

    def replay_acknowledgement(self, delivery_attempt_id: UUID) -> DeliveryAcknowledgement:
        return self._acknowledgement(delivery_attempt_id)

    def report_failure(
        self,
        claim: ClaimedDelivery,
        *,
        worker_id: str,
        failure_class: str,
    ) -> DeliveryFailureDisposition:
        row = self._connection.execute(
            "SELECT a.state, a.worker_id, a.lease_expires_at > clock_timestamp(), "
            "a.attempt_number, d.thread_id, d.retry_policy, d.status "
            "FROM openmagic_runtime.delivery_attempts AS a "
            "JOIN openmagic_runtime.deliveries AS d ON d.delivery_id = a.delivery_id "
            "WHERE a.delivery_attempt_id = %s AND a.delivery_id = %s "
            "FOR UPDATE OF a, d",
            (claim.delivery_attempt_id, claim.delivery_id),
        ).fetchone()
        if (
            row is None
            or row[0] != "running"
            or row[1] != worker_id
            or not row[2]
            or int(row[3]) != claim.attempt_number
            or UUID(str(row[4])) != claim.thread_id
            or row[6] != "pending"
        ):
            raise StaleDeliveryAuthority("Delivery Attempt authority is stale")
        policy = dict(row[5])
        retryable = tuple(policy["retryable_failure_classes"])
        terminal = tuple(policy["terminal_failure_classes"])
        if failure_class not in retryable and failure_class not in terminal:
            raise DeliveryProposalConflict("Delivery failure class is not qualified by policy")
        self._connection.execute(
            "UPDATE openmagic_runtime.delivery_attempts SET state = 'failed', "
            "completed_at = clock_timestamp() WHERE delivery_attempt_id = %s",
            (claim.delivery_attempt_id,),
        )
        if failure_class in retryable and claim.attempt_number < int(policy["max_attempts"]):
            delay = int(policy["delays_seconds"][claim.attempt_number - 1])
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

    def _claimed_from_ids(self, delivery_attempt_id: UUID) -> ClaimedDelivery:
        row = self._connection.execute(
            "SELECT a.delivery_id, a.attempt_number, d.thread_id, d.content_descriptor, "
            "d.context_through_sequence "
            "FROM openmagic_runtime.delivery_attempts AS a "
            "JOIN openmagic_runtime.deliveries AS d ON d.delivery_id = a.delivery_id "
            "WHERE a.delivery_attempt_id = %s",
            (delivery_attempt_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Delivery Attempt not found")
        return ClaimedDelivery(
            delivery_attempt_id=delivery_attempt_id,
            delivery_id=UUID(str(row[0])),
            attempt_number=int(row[1]),
            thread_id=UUID(str(row[2])),
            content_descriptor=dict(row[3]),
            context_through_sequence=int(row[4]),
        )

    def _acknowledgement(self, delivery_attempt_id: UUID) -> DeliveryAcknowledgement:
        row = self._connection.execute(
            "SELECT d.delivery_id, d.thread_id, d.delivered_message_id, m.sequence, "
            "d.acknowledged_at FROM openmagic_runtime.deliveries AS d "
            "JOIN openmagic_runtime.messages AS m ON m.message_id = d.delivered_message_id "
            "WHERE d.successful_attempt_id = %s AND d.status = 'delivered'",
            (delivery_attempt_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Delivery Acknowledgement does not exist")
        return DeliveryAcknowledgement(
            delivery_id=UUID(str(row[0])),
            delivery_attempt_id=delivery_attempt_id,
            thread_id=UUID(str(row[1])),
            message_id=UUID(str(row[2])),
            message_sequence=int(row[3]),
            acknowledged_at=row[4],
        )


def claim_delivery_once_record(
    *, database_url: str, request: ClaimDelivery
) -> ClaimedDelivery | None:
    try:
        with psycopg.connect(database_url) as connection, connection.transaction():
            return DeliveryWorkTransaction(connection).claim(request)
    except psycopg.errors.UniqueViolation as error:
        if error.diag.constraint_name == "one_running_delivery_attempt":
            return None
        raise


def acknowledge_delivery_record(
    *,
    database_url: str,
    claim: ClaimedDelivery,
    worker_id: str,
    proposed_thread_id: UUID,
) -> DeliveryAcknowledgement:
    with psycopg.connect(database_url) as connection, connection.transaction():
        return DeliveryWorkTransaction(connection).acknowledge(
            claim,
            worker_id=worker_id,
            proposed_thread_id=proposed_thread_id,
        )


__all__ = [
    "DeliveryControlTransaction",
    "DeliveryWorkTransaction",
    "acknowledge_delivery_record",
    "claim_delivery_once_record",
]
