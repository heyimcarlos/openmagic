"""Dependency-neutral contracts for leased Attempt work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID


class StaleAuthority(RuntimeError):
    """Raised when a Worker submits a result after its lease authority ended."""

    def __init__(
        self,
        message: str,
        *,
        checked_at: datetime | None = None,
        lease_expires_at: datetime | None = None,
    ) -> None:
        super().__init__(message)
        self.checked_at = checked_at
        self.lease_expires_at = lease_expires_at


class AttemptResultConflict(RuntimeError):
    """Raised when an Attempt identity is reused with a different observation."""


@dataclass(frozen=True)
class ClaimWork:
    claim_request_id: UUID
    worker_id: str
    executor_keys: tuple[str, ...]


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
