"""Claim, expiry, and replay persistence for Delivery work."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime._delivery_contracts import ClaimDelivery, ClaimedDelivery
from openmagic_runtime._persistence.delivery_work_models import (
    ClaimableDeliveryRecord,
    ClaimedDeliveryRecord,
    ExpiredDeliveryRecord,
)


class DeliveryClaimRecords:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def claim(self, request: ClaimDelivery) -> ClaimedDelivery | None:
        replay_id = self._replay_attempt_id(request.claim_request_id)
        if replay_id is not None:
            return self.claimed_delivery(replay_id)
        self._recover_one_expired()
        delivery = self._lock_claimable_delivery()
        if delivery is None:
            return None
        attempt_number = self._next_attempt_number(delivery.delivery_id)
        if attempt_number > delivery.policy.max_attempts:
            self._mark_delivery_failed(delivery.delivery_id)
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
                delivery.delivery_id,
                attempt_number,
                request.worker_id,
                delivery.policy.lease_seconds,
            ),
        )
        return ClaimedDelivery(
            delivery_attempt_id=attempt_id,
            delivery_id=delivery.delivery_id,
            attempt_number=attempt_number,
            thread_id=delivery.thread_id,
            content_descriptor=dict(delivery.content_descriptor),
            context_through_sequence=delivery.context_through_sequence,
        )

    def claimed_delivery(self, delivery_attempt_id: UUID) -> ClaimedDelivery:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT a.delivery_id, a.attempt_number, d.thread_id, "
                "d.content_descriptor, d.context_through_sequence "
                "FROM openmagic_runtime.delivery_attempts AS a "
                "JOIN openmagic_runtime.deliveries AS d ON d.delivery_id = a.delivery_id "
                "WHERE a.delivery_attempt_id = %s",
                (delivery_attempt_id,),
            ).fetchone()
        if record is None:
            raise RuntimeError("Delivery Attempt not found")
        return ClaimedDeliveryRecord.decode(record).claim(delivery_attempt_id)

    def _replay_attempt_id(self, claim_request_id: UUID) -> UUID | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT delivery_attempt_id FROM openmagic_runtime.delivery_attempts "
                "WHERE claim_request_id = %s",
                (claim_request_id,),
            ).fetchone()
        return None if record is None else UUID(str(record["delivery_attempt_id"]))

    def _recover_one_expired(self) -> None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT d.delivery_id, d.retry_policy, a.attempt_number "
                "FROM openmagic_runtime.deliveries AS d "
                "JOIN openmagic_runtime.delivery_attempts AS a "
                "ON a.delivery_id = d.delivery_id "
                "WHERE d.status = 'pending' AND a.state = 'running' "
                "AND a.lease_expires_at <= clock_timestamp() "
                "ORDER BY d.created_at, d.delivery_id FOR UPDATE OF d, a "
                "SKIP LOCKED LIMIT 1"
            ).fetchone()
        if record is None:
            return
        expired = ExpiredDeliveryRecord.decode(record)
        self._connection.execute(
            "UPDATE openmagic_runtime.delivery_attempts SET state = 'abandoned', "
            "completed_at = clock_timestamp() WHERE delivery_id = %s AND state = 'running' "
            "AND lease_expires_at <= clock_timestamp()",
            (expired.delivery_id,),
        )
        if expired.attempt_number >= expired.policy.max_attempts:
            self._mark_delivery_failed(expired.delivery_id)
            return
        delay = expired.policy.delays_seconds[expired.attempt_number - 1]
        self._connection.execute(
            "UPDATE openmagic_runtime.deliveries SET next_eligible_at = "
            "clock_timestamp() + (%s * interval '1 second') WHERE delivery_id = %s",
            (delay, expired.delivery_id),
        )

    def _lock_claimable_delivery(self) -> ClaimableDeliveryRecord | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT d.delivery_id, d.thread_id, d.content_descriptor, "
                "d.context_through_sequence, d.retry_policy "
                "FROM openmagic_runtime.deliveries AS d "
                "WHERE d.status = 'pending' AND d.next_eligible_at <= clock_timestamp() "
                "AND NOT EXISTS (SELECT 1 FROM openmagic_runtime.delivery_attempts AS a "
                "WHERE a.delivery_id = d.delivery_id AND a.state = 'running') "
                "ORDER BY d.next_eligible_at, d.delivery_id "
                "FOR UPDATE OF d SKIP LOCKED LIMIT 1"
            ).fetchone()
        return None if record is None else ClaimableDeliveryRecord.decode(record)

    def _next_attempt_number(self, delivery_id: UUID) -> int:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT count(*) AS attempt_count "
                "FROM openmagic_runtime.delivery_attempts WHERE delivery_id = %s",
                (delivery_id,),
            ).fetchone()
        return 1 if record is None else int(record["attempt_count"]) + 1

    def _mark_delivery_failed(self, delivery_id: UUID) -> None:
        self._connection.execute(
            "UPDATE openmagic_runtime.deliveries SET status = 'failed' WHERE delivery_id = %s",
            (delivery_id,),
        )


__all__ = ["DeliveryClaimRecords"]
