"""Typed Commands for renewal approval, revision, revocation, and cancellation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID, uuid5

from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.kernel.work import ClaimedAttempt

from example_insurance.renewal_effects import RenewalEmailEffect

DecisionOutcome = Literal[
    "approved",
    "revision_requested",
    "unauthorized_actor",
    "authority_revoked",
    "stale_presentation",
    "wait_already_satisfied",
]

_DISPATCH_COMMAND_NAMESPACE = UUID("d225feef-9dc0-469c-ad8d-56ca33702ff8")
_EFFECT_OBSERVATION_COMMAND_NAMESPACE = UUID("e78493d6-3757-4b0a-9aba-e14ea52558a8")


@dataclass(frozen=True)
class StartRenewalOutreachInput:
    workflow_id: UUID
    thread_id: UUID
    policy_id: UUID
    policy_number: str
    policyholder_name: str
    policyholder_email: str
    renewal_date: str
    expiring_premium_cents: int


@dataclass(frozen=True)
class StartRenewalOutreach:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: StartRenewalOutreachInput


@dataclass(frozen=True)
class StartRenewalOutreachResult:
    workflow_id: UUID
    instance_id: UUID
    thread_id: UUID


@dataclass(frozen=True)
class WorkflowAttemptResult:
    attempt_id: UUID
    template_key: str
    executor_key: str
    agent_run_id: UUID | None
    agent_runtime_generation: int | None
    steps: dict[str, UUID]
    waits: dict[str, UUID]


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


@dataclass(frozen=True)
class AuthorizeRenewalEmailDispatchInput:
    attempt: ClaimedAttempt
    worker_id: str


@dataclass(frozen=True)
class AuthorizeRenewalEmailDispatch:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: AuthorizeRenewalEmailDispatchInput


@dataclass(frozen=True)
class RenewalEffectObservation:
    classification: Literal["applied", "not_applied", "uncertain"]
    provider_request_id: str


@dataclass(frozen=True)
class AcceptRenewalEffectObservationInput:
    attempt: ClaimedAttempt
    worker_id: str
    observation: RenewalEffectObservation


@dataclass(frozen=True)
class AcceptRenewalEffectObservation:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: AcceptRenewalEffectObservationInput


def dispatch_command_id(attempt_id: UUID) -> UUID:
    return uuid5(_DISPATCH_COMMAND_NAMESPACE, str(attempt_id))


def effect_observation_command_id(attempt_id: UUID) -> UUID:
    return uuid5(_EFFECT_OBSERVATION_COMMAND_NAMESPACE, str(attempt_id))


def validate_approval(command: ApproveRenewalDraft) -> None:
    if not command.actor.identifier.strip() or not command.cause.identifier.strip():
        raise ValueError("Command Actor and Cause identifiers must be non-empty")
    if not command.input.presentation_fingerprint.strip():
        raise ValueError("Presentation fingerprint must be non-empty")


def validate_start(command: StartRenewalOutreach) -> None:
    value = command.input
    if not command.actor.identifier.strip() or not command.cause.identifier.strip():
        raise ValueError("Command Actor and Cause identifiers must be non-empty")
    if (
        not value.policy_number.strip()
        or not value.policyholder_name.strip()
        or not value.policyholder_email.strip()
        or "@" not in value.policyholder_email
    ):
        raise ValueError("Policy number, policyholder name, and email must be valid")
    date.fromisoformat(value.renewal_date)
    if value.expiring_premium_cents <= 0:
        raise ValueError("Expiring premium must be positive")


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


def validate_dispatch(command: AuthorizeRenewalEmailDispatch) -> None:
    if (
        command.actor.kind != "system"
        or command.actor.identifier != command.input.worker_id
        or command.cause.kind != "attempt"
        or command.cause.identifier != str(command.input.attempt.attempt_id)
    ):
        raise ValueError("Dispatch authorization must identify the exact Worker Attempt")


def validate_effect_observation(command: AcceptRenewalEffectObservation) -> None:
    validate_dispatch(
        AuthorizeRenewalEmailDispatch(
            command.command_id,
            command.actor,
            command.cause,
            AuthorizeRenewalEmailDispatchInput(
                command.input.attempt,
                command.input.worker_id,
            ),
        )
    )
    if not command.input.observation.provider_request_id.strip():
        raise ValueError("Effect observation requires a provider request identity")


__all__ = [
    "AcceptRenewalEffectObservation",
    "AcceptRenewalEffectObservationInput",
    "ApproveRenewalDraft",
    "ApproveRenewalDraftInput",
    "ApproveRenewalDraftResult",
    "AuthorizeRenewalEmailDispatch",
    "AuthorizeRenewalEmailDispatchInput",
    "CancelRenewalOutreach",
    "CancelRenewalOutreachInput",
    "CancelRenewalOutreachResult",
    "DecisionOutcome",
    "RenewalEffectObservation",
    "RequestRenewalRevision",
    "RequestRenewalRevisionInput",
    "RequestRenewalRevisionResult",
    "RevokeRenewalAuthority",
    "RevokeRenewalAuthorityInput",
    "RevokeRenewalAuthorityResult",
    "StartRenewalOutreach",
    "StartRenewalOutreachInput",
    "StartRenewalOutreachResult",
    "WorkflowAttemptResult",
    "dispatch_command_id",
    "effect_observation_command_id",
    "validate_approval",
    "validate_cancellation",
    "validate_dispatch",
    "validate_effect_observation",
    "validate_revision",
    "validate_revocation",
    "validate_start",
]
