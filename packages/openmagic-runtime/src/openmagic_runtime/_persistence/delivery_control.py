"""Transaction-bound composition of cohesive Delivery persistence owners."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection

from openmagic_runtime._delivery_contracts import (
    ClaimDelivery,
    ClaimedDelivery,
    DeliveryAcknowledgement,
    DeliveryFailureDisposition,
    DeliveryIntent,
    DeliveryRetryPolicy,
)
from openmagic_runtime._persistence.delivery_claims import DeliveryClaimRecords
from openmagic_runtime._persistence.delivery_intents import DeliveryIntentRecords
from openmagic_runtime._persistence.delivery_results import DeliveryResultRecords


class DeliveryControlTransaction:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._intents = DeliveryIntentRecords(connection)

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
        return self._intents.create(
            domain_event_id=domain_event_id,
            thread_id=thread_id,
            audience=audience,
            message_author=message_author,
            content_descriptor=content_descriptor,
            message_content=message_content,
            retry_policy=retry_policy,
        )


class DeliveryWorkTransaction:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._claims = DeliveryClaimRecords(connection)
        self._results = DeliveryResultRecords(connection)

    def claim(self, request: ClaimDelivery) -> ClaimedDelivery | None:
        return self._claims.claim(request)

    def acknowledge(
        self,
        claim: ClaimedDelivery,
        *,
        worker_id: str,
        proposed_thread_id: UUID,
    ) -> DeliveryAcknowledgement:
        return self._results.acknowledge(
            claim,
            worker_id=worker_id,
            proposed_thread_id=proposed_thread_id,
        )

    def replay_acknowledgement(self, delivery_attempt_id: UUID) -> DeliveryAcknowledgement:
        return self._results.acknowledgement(delivery_attempt_id)

    def report_failure(
        self,
        claim: ClaimedDelivery,
        *,
        worker_id: str,
        failure_class: str,
    ) -> DeliveryFailureDisposition:
        return self._results.report_failure(
            claim,
            worker_id=worker_id,
            failure_class=failure_class,
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
