"""Named immutable records for Delivery authority reconstruction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from openmagic_runtime._delivery_contracts import (
    ClaimedDelivery,
    DeliveryAcknowledgement,
    DeliveryRetryPolicy,
)


def _string_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise RuntimeError("Delivery Retry Policy has an invalid durable representation")
    return {str(key): item for key, item in value.items()}


def _items(value: object) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise RuntimeError("Delivery Retry Policy has an invalid durable representation")
    return tuple(value)


def retry_policy(value: object) -> DeliveryRetryPolicy:
    record = _string_mapping(value)
    return DeliveryRetryPolicy(
        version=int(record["version"]),
        max_attempts=int(record["max_attempts"]),
        delays_seconds=tuple(int(item) for item in _items(record["delays_seconds"])),
        lease_seconds=int(record["lease_seconds"]),
        retryable_failure_classes=tuple(
            str(item) for item in _items(record["retryable_failure_classes"])
        ),
        terminal_failure_classes=tuple(
            str(item) for item in _items(record["terminal_failure_classes"])
        ),
    )


@dataclass(frozen=True)
class ExpiredDeliveryRecord:
    delivery_id: UUID
    policy: DeliveryRetryPolicy
    attempt_number: int

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ExpiredDeliveryRecord:
        return cls(
            delivery_id=UUID(str(record["delivery_id"])),
            policy=retry_policy(record["retry_policy"]),
            attempt_number=int(record["attempt_number"]),
        )


@dataclass(frozen=True)
class ClaimableDeliveryRecord:
    delivery_id: UUID
    thread_id: UUID
    content_descriptor: dict[str, Any]
    context_through_sequence: int
    policy: DeliveryRetryPolicy

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ClaimableDeliveryRecord:
        return cls(
            delivery_id=UUID(str(record["delivery_id"])),
            thread_id=UUID(str(record["thread_id"])),
            content_descriptor=dict(record["content_descriptor"]),
            context_through_sequence=int(record["context_through_sequence"]),
            policy=retry_policy(record["retry_policy"]),
        )


@dataclass(frozen=True)
class ClaimedDeliveryRecord:
    delivery_id: UUID
    attempt_number: int
    thread_id: UUID
    content_descriptor: dict[str, Any]
    context_through_sequence: int

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ClaimedDeliveryRecord:
        return cls(
            delivery_id=UUID(str(record["delivery_id"])),
            attempt_number=int(record["attempt_number"]),
            thread_id=UUID(str(record["thread_id"])),
            content_descriptor=dict(record["content_descriptor"]),
            context_through_sequence=int(record["context_through_sequence"]),
        )

    def claim(self, delivery_attempt_id: UUID) -> ClaimedDelivery:
        return ClaimedDelivery(
            delivery_attempt_id=delivery_attempt_id,
            delivery_id=self.delivery_id,
            attempt_number=self.attempt_number,
            thread_id=self.thread_id,
            content_descriptor=dict(self.content_descriptor),
            context_through_sequence=self.context_through_sequence,
        )


@dataclass(frozen=True)
class DeliveryRecord:
    thread_id: UUID
    status: str
    successful_attempt_id: UUID | None
    message_author: dict[str, Any]
    message_content: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DeliveryRecord:
        successful_attempt_id = record["successful_attempt_id"]
        return cls(
            thread_id=UUID(str(record["thread_id"])),
            status=str(record["status"]),
            successful_attempt_id=None
            if successful_attempt_id is None
            else UUID(str(successful_attempt_id)),
            message_author=dict(record["message_author"]),
            message_content=str(record["message_content"]),
        )


@dataclass(frozen=True)
class DeliveryAttemptAuthorityRecord:
    state: str
    worker_id: str
    lease_valid: bool

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DeliveryAttemptAuthorityRecord:
        return cls(
            state=str(record["state"]),
            worker_id=str(record["worker_id"]),
            lease_valid=bool(record["lease_valid"]),
        )


@dataclass(frozen=True)
class DeliveryFailureAuthorityRecord:
    state: str
    worker_id: str
    lease_valid: bool
    attempt_number: int
    thread_id: UUID
    policy: DeliveryRetryPolicy
    delivery_status: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DeliveryFailureAuthorityRecord:
        return cls(
            state=str(record["state"]),
            worker_id=str(record["worker_id"]),
            lease_valid=bool(record["lease_valid"]),
            attempt_number=int(record["attempt_number"]),
            thread_id=UUID(str(record["thread_id"])),
            policy=retry_policy(record["retry_policy"]),
            delivery_status=str(record["delivery_status"]),
        )


@dataclass(frozen=True)
class DeliveryAcknowledgementRecord:
    delivery_id: UUID
    thread_id: UUID
    message_id: UUID
    message_sequence: int
    acknowledged_at: datetime

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DeliveryAcknowledgementRecord:
        return cls(
            delivery_id=UUID(str(record["delivery_id"])),
            thread_id=UUID(str(record["thread_id"])),
            message_id=UUID(str(record["delivered_message_id"])),
            message_sequence=int(record["sequence"]),
            acknowledged_at=record["acknowledged_at"],
        )

    def acknowledgement(self, delivery_attempt_id: UUID) -> DeliveryAcknowledgement:
        return DeliveryAcknowledgement(
            delivery_id=self.delivery_id,
            delivery_attempt_id=delivery_attempt_id,
            thread_id=self.thread_id,
            message_id=self.message_id,
            message_sequence=self.message_sequence,
            acknowledged_at=self.acknowledged_at,
        )


__all__ = [
    "ClaimableDeliveryRecord",
    "ClaimedDeliveryRecord",
    "DeliveryAcknowledgementRecord",
    "DeliveryAttemptAuthorityRecord",
    "DeliveryFailureAuthorityRecord",
    "DeliveryRecord",
    "ExpiredDeliveryRecord",
    "retry_policy",
]
