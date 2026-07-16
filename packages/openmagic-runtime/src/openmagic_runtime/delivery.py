"""Durable exact-Thread Delivery policy over private persistence records."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import Connection

from openmagic_runtime._delivery_contracts import (
    ClaimDelivery,
    ClaimedDelivery,
    DeliveryAcknowledgement,
    DeliveryAttemptState,
    DeliveryFailureDisposition,
    DeliveryIntent,
    DeliveryProposalConflict,
    DeliveryRetryPolicy,
    DeliveryStatus,
    StaleDeliveryAuthority,
    delivery_attempt_state,
    delivery_status,
)
from openmagic_runtime._persistence.delivery_claims import (
    DeliveryClaimRecords,
    claim_delivery_once_record,
)
from openmagic_runtime._persistence.delivery_intents import DeliveryIntentRecords
from openmagic_runtime._persistence.delivery_records import (
    DeliveredMessage,
    DeliveryPresentation,
    RuntimeDeliveryEvidence,
    deliveries_for_domain_event,
    lock_delivery_presentation,
    read_delivery_presentation,
)
from openmagic_runtime._persistence.delivery_results import (
    DeliveryResultRecords,
    acknowledge_delivery_record,
)


class DeliveryControl:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._records = DeliveryIntentRecords(connection)

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
        return self._records.create(
            domain_event_id=domain_event_id,
            thread_id=thread_id,
            audience=audience,
            message_author=message_author,
            content_descriptor=content_descriptor,
            message_content=message_content,
            retry_policy=retry_policy,
        )


class DeliveryWork:
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


def claim_delivery_once(*, database_url: str, request: ClaimDelivery) -> ClaimedDelivery | None:
    return claim_delivery_once_record(database_url=database_url, request=request)


def acknowledge_delivery(
    *,
    database_url: str,
    claim: ClaimedDelivery,
    worker_id: str,
    proposed_thread_id: UUID,
) -> DeliveryAcknowledgement:
    return acknowledge_delivery_record(
        database_url=database_url,
        claim=claim,
        worker_id=worker_id,
        proposed_thread_id=proposed_thread_id,
    )


__all__ = [
    "ClaimDelivery",
    "ClaimedDelivery",
    "DeliveredMessage",
    "DeliveryAcknowledgement",
    "DeliveryAttemptState",
    "DeliveryControl",
    "DeliveryFailureDisposition",
    "DeliveryIntent",
    "DeliveryPresentation",
    "DeliveryProposalConflict",
    "DeliveryRetryPolicy",
    "DeliveryStatus",
    "DeliveryWork",
    "RuntimeDeliveryEvidence",
    "StaleDeliveryAuthority",
    "acknowledge_delivery",
    "claim_delivery_once",
    "deliveries_for_domain_event",
    "delivery_attempt_state",
    "delivery_status",
    "lock_delivery_presentation",
    "read_delivery_presentation",
]
