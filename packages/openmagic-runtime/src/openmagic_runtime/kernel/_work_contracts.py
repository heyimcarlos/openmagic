"""Dependency-neutral contracts for leased Attempt work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID


class StaleAuthority(RuntimeError):
    """Raised when a Worker submits a result after its lease authority ended."""


class AttemptResultConflict(RuntimeError):
    """Raised when an Attempt identity is reused with a different observation."""


@dataclass(frozen=True)
class ClaimWork:
    claim_request_id: UUID
    worker_id: str
    executor_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.claim_request_id, UUID):
            raise ValueError("Claim Request identity must be a UUID")
        if not isinstance(self.worker_id, str) or not self.worker_id.strip():
            raise ValueError("Attempt claim worker must be non-empty")
        if (
            type(self.executor_keys) is not tuple
            or not self.executor_keys
            or any(not isinstance(key, str) or not key.strip() for key in self.executor_keys)
            or len(set(self.executor_keys)) != len(self.executor_keys)
        ):
            raise ValueError("Attempt claim executor keys must be unique and non-empty")


@dataclass(frozen=True)
class ClaimedAttempt:
    instance_id: UUID
    step_id: UUID
    attempt_id: UUID
    attempt_number: int
    template_key: str
    executor_key: str
    lease_seconds: int
    input: dict[str, Any]

    def __post_init__(self) -> None:
        identities = (self.instance_id, self.step_id, self.attempt_id)
        if any(not isinstance(value, UUID) for value in identities):
            raise ValueError("Claimed Attempt identities must be UUIDs")
        if type(self.attempt_number) is not int or self.attempt_number <= 0:
            raise ValueError("Claimed Attempt number must be positive")
        if type(self.lease_seconds) is not int or self.lease_seconds <= 0:
            raise ValueError("Claimed Attempt lease must be positive")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.template_key, self.executor_key)
        ):
            raise ValueError("Claimed Attempt keys must be non-empty")
        if not isinstance(self.input, dict) or any(not isinstance(key, str) for key in self.input):
            raise ValueError("Claimed Attempt input must be a string-keyed mapping")


@dataclass(frozen=True)
class RenewedAttemptLease:
    attempt_id: UUID
    lease_expires_at: datetime
    hard_deadline: datetime


@dataclass(frozen=True)
class AttemptExecutionAuthority:
    claim: ClaimedAttempt
    directive: Literal["execute", "replay"]
    accepted_observation: dict[str, Any] | None

    def __post_init__(self) -> None:
        replay = self.directive == "replay"
        if replay != (self.accepted_observation is not None):
            raise ValueError("Attempt execution authority has an inconsistent directive")


@dataclass
class DispositionRequired:
    instance_id: UUID
    step_id: UUID
    attempt_id: UUID
    attempt_number: int
    template_key: str
    observation: dict[str, Any]
    basis_state: Literal["completed", "abandoned"] = "completed"
    consumed: bool = False
    replayed: bool = False


__all__ = [
    "AttemptExecutionAuthority",
    "AttemptResultConflict",
    "ClaimWork",
    "ClaimedAttempt",
    "DispositionRequired",
    "RenewedAttemptLease",
    "StaleAuthority",
]
