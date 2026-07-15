"""Typed Commands for renewal approval, revision, revocation, and cancellation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from openmagic_runtime.commands import Actor, Cause

from example_insurance.renewal_effects import RenewalEmailEffect

DecisionOutcome = Literal[
    "approved",
    "revision_requested",
    "unauthorized_actor",
    "authority_revoked",
    "stale_presentation",
    "wait_already_satisfied",
]


@dataclass(frozen=True)
class ApproveRenewalDraftInput:
    workflow_id: UUID
    wait_id: UUID
    draft_id: UUID
    presentation_fingerprint: str
    proposed_effect: RenewalEmailEffect


@dataclass(frozen=True)
class ApproveRenewalDraft:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: ApproveRenewalDraftInput


@dataclass(frozen=True)
class ApproveRenewalDraftResult:
    outcome: DecisionOutcome
    workflow_id: UUID
    wait_id: UUID
    approval_grant_id: UUID | None
    effect_step_id: UUID | None


@dataclass(frozen=True)
class RequestRenewalRevisionInput:
    workflow_id: UUID
    wait_id: UUID
    draft_id: UUID
    presentation_fingerprint: str
    proposed_effect: RenewalEmailEffect
    revision_instruction: str


@dataclass(frozen=True)
class RequestRenewalRevision:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: RequestRenewalRevisionInput


@dataclass(frozen=True)
class RequestRenewalRevisionResult:
    outcome: DecisionOutcome
    workflow_id: UUID
    wait_id: UUID
    revision_step_id: UUID | None


@dataclass(frozen=True)
class RevokeRenewalAuthorityInput:
    workflow_id: UUID
    actor_id: str


@dataclass(frozen=True)
class RevokeRenewalAuthority:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: RevokeRenewalAuthorityInput


@dataclass(frozen=True)
class RevokeRenewalAuthorityResult:
    outcome: Literal["revoked", "already_revoked"]
    workflow_id: UUID


@dataclass(frozen=True)
class CancelRenewalOutreachInput:
    workflow_id: UUID


@dataclass(frozen=True)
class CancelRenewalOutreach:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: CancelRenewalOutreachInput


@dataclass(frozen=True)
class CancelRenewalOutreachResult:
    outcome: Literal["cancelled", "too_late", "already_completed", "already_cancelled"]
    workflow_id: UUID
    instance_id: UUID


def validate_approval(command: ApproveRenewalDraft) -> None:
    if not command.actor.identifier.strip() or not command.cause.identifier.strip():
        raise ValueError("Command Actor and Cause identifiers must be non-empty")
    if not command.input.presentation_fingerprint.strip():
        raise ValueError("Presentation fingerprint must be non-empty")


def validate_revision(command: RequestRenewalRevision) -> None:
    if not command.actor.identifier.strip() or not command.cause.identifier.strip():
        raise ValueError("Command Actor and Cause identifiers must be non-empty")
    if not command.input.presentation_fingerprint.strip():
        raise ValueError("Presentation fingerprint must be non-empty")
    if not command.input.revision_instruction.strip():
        raise ValueError("Revision instruction must be non-empty")


def validate_revocation(command: RevokeRenewalAuthority) -> None:
    if command.actor.kind != "system" or not command.input.actor_id.strip():
        raise ValueError("Authority revocation requires a System Actor and exact Party identity")


def validate_cancellation(command: CancelRenewalOutreach) -> None:
    if not command.actor.identifier.strip() or not command.cause.identifier.strip():
        raise ValueError("Command Actor and Cause identifiers must be non-empty")


__all__ = [
    "ApproveRenewalDraft",
    "ApproveRenewalDraftInput",
    "ApproveRenewalDraftResult",
    "CancelRenewalOutreach",
    "CancelRenewalOutreachInput",
    "CancelRenewalOutreachResult",
    "DecisionOutcome",
    "RequestRenewalRevision",
    "RequestRenewalRevisionInput",
    "RequestRenewalRevisionResult",
    "RevokeRenewalAuthority",
    "RevokeRenewalAuthorityInput",
    "RevokeRenewalAuthorityResult",
    "validate_approval",
    "validate_cancellation",
    "validate_revision",
    "validate_revocation",
]
