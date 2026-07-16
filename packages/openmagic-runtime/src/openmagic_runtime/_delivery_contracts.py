"""Dependency-neutral contracts for durable Delivery control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID


class StaleDeliveryAuthority(RuntimeError):
    """Raised when a Delivery Worker no longer owns the leased Attempt."""


class DeliveryProposalConflict(RuntimeError):
    """Raised when a proposal differs from the frozen Delivery intent."""


@dataclass(frozen=True)
class DeliveryRetryPolicy:
    version: int
    max_attempts: int
    delays_seconds: tuple[int, ...]
    lease_seconds: int
    retryable_failure_classes: tuple[str, ...]
    terminal_failure_classes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version <= 0 or self.max_attempts <= 0 or self.lease_seconds <= 0:
            raise ValueError("Delivery Retry Policy values must be positive")
        if len(self.delays_seconds) != self.max_attempts - 1:
            raise ValueError("Delivery Retry Policy must define every retry delay")
        if any(delay < 0 for delay in self.delays_seconds):
            raise ValueError("Delivery retry delays cannot be negative")
        classifications = self.retryable_failure_classes + self.terminal_failure_classes
        if (
            not classifications
            or any(not item for item in classifications)
            or len(classifications) != len(set(classifications))
        ):
            raise ValueError("Delivery failure classes must be non-empty and disjoint")


@dataclass(frozen=True)
class DeliveryFailureDisposition:
    delivery_id: UUID
    delivery_attempt_id: UUID
    outcome: Literal["retry_scheduled", "failed"]


@dataclass(frozen=True)
class DeliveryIntent:
    delivery_id: UUID
    domain_event_id: UUID
    thread_id: UUID
    context_through_sequence: int


@dataclass(frozen=True)
class ClaimDelivery:
    claim_request_id: UUID
    worker_id: str


@dataclass(frozen=True)
class ClaimedDelivery:
    delivery_attempt_id: UUID
    delivery_id: UUID
    attempt_number: int
    thread_id: UUID
    content_descriptor: dict[str, Any]
    context_through_sequence: int


@dataclass(frozen=True)
class DeliveryAcknowledgement:
    delivery_id: UUID
    delivery_attempt_id: UUID
    thread_id: UUID
    message_id: UUID
    message_sequence: int
    acknowledged_at: datetime


__all__ = [
    "ClaimDelivery",
    "ClaimedDelivery",
    "DeliveryAcknowledgement",
    "DeliveryFailureDisposition",
    "DeliveryIntent",
    "DeliveryProposalConflict",
    "DeliveryRetryPolicy",
    "StaleDeliveryAuthority",
]
