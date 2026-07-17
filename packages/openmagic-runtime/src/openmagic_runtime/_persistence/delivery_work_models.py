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
    DeliveryAttemptState,
    DeliveryRetryPolicy,
    DeliveryStatus,
    delivery_attempt_state,
    delivery_status,
)
from openmagic_runtime._persistence.durable_values import (
    boolean_value,
    integer_items,
    integer_value,
    invalid_durable_value,
    mapping_value,
    nonempty_mapping,
    nonempty_string,
    nonnegative_integer_value,
    positive_integer_value,
    string_items,
    timestamp_value,
    uuid_value,
)


def retry_policy(value: object) -> DeliveryRetryPolicy:
    record = mapping_value(value)
    if set(record) != {
        "version",
        "max_attempts",
        "delays_seconds",
        "lease_seconds",
        "retryable_failure_classes",
        "terminal_failure_classes",
    }:
        raise invalid_durable_value()
    return DeliveryRetryPolicy(
        version=integer_value(record["version"]),
        max_attempts=integer_value(record["max_attempts"]),
        delays_seconds=integer_items(record["delays_seconds"]),
        lease_seconds=integer_value(record["lease_seconds"]),
        retryable_failure_classes=string_items(record["retryable_failure_classes"]),
        terminal_failure_classes=string_items(record["terminal_failure_classes"]),
    )


@dataclass(frozen=True)
class ExpiredDeliveryContextRecord:
    delivery_id: UUID
    policy: DeliveryRetryPolicy

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ExpiredDeliveryContextRecord:
        return cls(
            delivery_id=uuid_value(record["delivery_id"]),
            policy=retry_policy(record["retry_policy"]),
        )


@dataclass(frozen=True)
class ExpiredDeliveryAttemptRecord:
    attempt_number: int

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ExpiredDeliveryAttemptRecord:
        return cls(
            attempt_number=positive_integer_value(record["attempt_number"]),
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
            delivery_id=uuid_value(record["delivery_id"]),
            thread_id=uuid_value(record["thread_id"]),
            content_descriptor=nonempty_mapping(record["content_descriptor"]),
            context_through_sequence=nonnegative_integer_value(record["context_through_sequence"]),
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
            delivery_id=uuid_value(record["delivery_id"]),
            attempt_number=positive_integer_value(record["attempt_number"]),
            thread_id=uuid_value(record["thread_id"]),
            content_descriptor=nonempty_mapping(record["content_descriptor"]),
            context_through_sequence=nonnegative_integer_value(record["context_through_sequence"]),
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
class DeliveryClaimReplayRecord:
    delivery_attempt_id: UUID
    worker_id: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DeliveryClaimReplayRecord:
        return cls(
            delivery_attempt_id=uuid_value(record["delivery_attempt_id"]),
            worker_id=nonempty_string(record["worker_id"]),
        )


@dataclass(frozen=True)
class MessageAuthorRecord:
    kind: str
    identifier: str

    @classmethod
    def decode(cls, value: object) -> MessageAuthorRecord:
        record = mapping_value(value)
        if set(record) != {"kind", "identifier"}:
            raise RuntimeError("Message author has an invalid durable representation")
        return cls(
            kind=nonempty_string(record["kind"]),
            identifier=nonempty_string(record["identifier"]),
        )


@dataclass(frozen=True)
class DeliveryRecord:
    thread_id: UUID
    status: DeliveryStatus
    successful_attempt_id: UUID | None
    message_author: MessageAuthorRecord
    message_content: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DeliveryRecord:
        successful_attempt_id = record["successful_attempt_id"]
        return cls(
            thread_id=uuid_value(record["thread_id"]),
            status=delivery_status(record["status"]),
            successful_attempt_id=None
            if successful_attempt_id is None
            else uuid_value(successful_attempt_id),
            message_author=MessageAuthorRecord.decode(record["message_author"]),
            message_content=nonempty_string(record["message_content"]),
        )


@dataclass(frozen=True)
class DeliveryAttemptAuthorityRecord:
    state: DeliveryAttemptState
    worker_id: str
    lease_valid: bool

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DeliveryAttemptAuthorityRecord:
        return cls(
            state=delivery_attempt_state(record["state"]),
            worker_id=nonempty_string(record["worker_id"]),
            lease_valid=boolean_value(record["lease_valid"]),
        )


@dataclass(frozen=True)
class DeliveryFailureContextRecord:
    thread_id: UUID
    policy: DeliveryRetryPolicy
    status: DeliveryStatus

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DeliveryFailureContextRecord:
        return cls(
            thread_id=uuid_value(record["thread_id"]),
            policy=retry_policy(record["retry_policy"]),
            status=delivery_status(record["status"]),
        )


@dataclass(frozen=True)
class DeliveryFailureAttemptRecord:
    state: DeliveryAttemptState
    worker_id: str
    lease_valid: bool
    attempt_number: int

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DeliveryFailureAttemptRecord:
        return cls(
            state=delivery_attempt_state(record["state"]),
            worker_id=nonempty_string(record["worker_id"]),
            lease_valid=boolean_value(record["lease_valid"]),
            attempt_number=integer_value(record["attempt_number"]),
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
            delivery_id=uuid_value(record["delivery_id"]),
            thread_id=uuid_value(record["thread_id"]),
            message_id=uuid_value(record["delivered_message_id"]),
            message_sequence=integer_value(record["sequence"]),
            acknowledged_at=timestamp_value(record["acknowledged_at"]),
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
    "DeliveryFailureAttemptRecord",
    "DeliveryFailureContextRecord",
    "DeliveryRecord",
    "ExpiredDeliveryAttemptRecord",
    "ExpiredDeliveryContextRecord",
    "MessageAuthorRecord",
]
