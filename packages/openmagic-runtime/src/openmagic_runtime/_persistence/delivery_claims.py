"""Claim, expiry, and replay persistence for Delivery work."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime._delivery_contracts import ClaimDelivery, ClaimedDelivery
from openmagic_runtime._persistence.delivery_work_models import (
    ClaimableDeliveryRecord,
    ClaimedDeliveryRecord,
    DeliveryClaimReplayRecord,
    ExpiredDeliveryAttemptRecord,
    ExpiredDeliveryContextRecord,
)
from openmagic_runtime._persistence.durable_values import nonnegative_integer_value


class DeliveryClaimRecords:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def claim(self, request: ClaimDelivery) -> ClaimedDelivery | None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (str(request.claim_request_id),),
        )
        replay = self._replay(request.claim_request_id)
        if replay is not None:
            if replay.worker_id != request.worker_id:
                raise ValueError("Delivery claim identity has conflicting input")
            return self.claimed_delivery(replay.delivery_attempt_id)
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

    def _replay(self, claim_request_id: UUID) -> DeliveryClaimReplayRecord | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT delivery_attempt_id, worker_id "
                "FROM openmagic_runtime.delivery_attempts "
                "WHERE claim_request_id = %s",
                (claim_request_id,),
            ).fetchone()
        return None if record is None else DeliveryClaimReplayRecord.decode(record)

    def _recover_one_expired(self) -> None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            delivery_record = cursor.execute(
                "SELECT d.delivery_id, d.retry_policy "
                "FROM openmagic_runtime.deliveries AS d "
                "WHERE d.status = 'pending' AND EXISTS ("
                "SELECT 1 FROM openmagic_runtime.delivery_attempts AS a "
                "WHERE a.delivery_id = d.delivery_id AND a.state = 'running' "
                "AND a.lease_expires_at <= clock_timestamp()) "
                "ORDER BY d.created_at, d.delivery_id "
                "FOR UPDATE OF d SKIP LOCKED LIMIT 1"
            ).fetchone()
            if delivery_record is None:
                return
            expired = ExpiredDeliveryContextRecord.decode(delivery_record)
            attempt_record = cursor.execute(
                "SELECT attempt_number FROM openmagic_runtime.delivery_attempts "
                "WHERE delivery_id = %s AND state = 'running' "
                "AND lease_expires_at <= clock_timestamp() "
                "ORDER BY created_at, delivery_attempt_id FOR UPDATE LIMIT 1",
                (expired.delivery_id,),
            ).fetchone()
        if attempt_record is None:
            return
        attempt = ExpiredDeliveryAttemptRecord.decode(attempt_record)
        self._connection.execute(
            "UPDATE openmagic_runtime.delivery_attempts SET state = 'abandoned', "
            "completed_at = clock_timestamp() WHERE delivery_id = %s AND state = 'running' "
            "AND lease_expires_at <= clock_timestamp()",
            (expired.delivery_id,),
        )
        if attempt.attempt_number >= expired.policy.max_attempts:
            self._mark_delivery_failed(expired.delivery_id)
            return
        delay = expired.policy.delays_seconds[attempt.attempt_number - 1]
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
        return 1 if record is None else nonnegative_integer_value(record["attempt_count"]) + 1

    def _mark_delivery_failed(self, delivery_id: UUID) -> None:
        self._connection.execute(
            "UPDATE openmagic_runtime.deliveries SET status = 'failed' WHERE delivery_id = %s",
            (delivery_id,),
        )


def claim_delivery_once_record(
    *, database_url: str, request: ClaimDelivery
) -> ClaimedDelivery | None:
    try:
        with psycopg.connect(database_url) as connection, connection.transaction():
            return DeliveryClaimRecords(connection).claim(request)
    except psycopg.errors.UniqueViolation as error:
        if error.diag.constraint_name == "one_running_delivery_attempt":
            return None
        raise


__all__ = ["DeliveryClaimRecords", "claim_delivery_once_record"]
