"""Typed application Commands for deterministic step-up verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from openmagic_runtime.commands import Actor, Cause

VerificationPurpose = Literal["renewal.read_approved_details"]
VerificationAuthorityTarget = Literal["identifier", "membership", "workflow_role"]
ProtectedOutcome = Literal[
    "authorized",
    "approval_required",
    "authority_revoked",
    "identifier_revoked",
    "workflow_closed",
    "wrong_party",
    "wrong_purpose",
    "wrong_thread",
]
VerificationCodeOutcome = Literal[
    "verified",
    "invalid_code",
    "expired",
    "already_used",
    "delivery_unconfirmed",
    "delivery_failed",
    "identifier_revoked",
    "authority_revoked",
    "workflow_closed",
    "wrong_party",
    "wrong_protected_command",
    "wrong_thread",
    "wrong_workflow",
    "wrong_purpose",
]


@dataclass(frozen=True)
class ProvisionVerificationAuthorityInput:
    party_id: UUID
    organization_party_id: UUID
    workflow_id: UUID
    email: str
    delivery_thread_id: UUID


@dataclass(frozen=True)
class ProvisionVerificationAuthority:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: ProvisionVerificationAuthorityInput


@dataclass(frozen=True)
class ProvisionVerificationAuthorityResult:
    outcome: Literal["provisioned"]
    party_id: UUID
    identifier_id: UUID
    membership_id: UUID
    participant_id: UUID


@dataclass(frozen=True)
class RevokeVerificationAuthorityInput:
    party_id: UUID
    workflow_id: UUID
    target: VerificationAuthorityTarget


@dataclass(frozen=True)
class RevokeVerificationAuthority:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: RevokeVerificationAuthorityInput


@dataclass(frozen=True)
class RevokeVerificationAuthorityResult:
    outcome: Literal["revoked", "already_revoked"]
    party_id: UUID
    workflow_id: UUID
    target: VerificationAuthorityTarget


@dataclass(frozen=True)
class RequestProtectedRenewalDetailsInput:
    workflow_id: UUID
    thread_id: UUID
    purpose: str
    approval_grant_id: UUID


@dataclass(frozen=True)
class RequestProtectedRenewalDetails:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: RequestProtectedRenewalDetailsInput


@dataclass(frozen=True)
class RequestProtectedRenewalDetailsResult:
    outcome: ProtectedOutcome | Literal["verification_required"]
    workflow_id: UUID
    challenge_id: UUID | None
    verification_workflow_id: UUID | None
    verification_instance_id: UUID | None
    authorized_delivery_id: UUID | None

    def __post_init__(self) -> None:
        verification_ids = (
            self.challenge_id,
            self.verification_workflow_id,
            self.verification_instance_id,
        )
        if self.outcome == "verification_required":
            if (
                any(value is None for value in verification_ids)
                or self.authorized_delivery_id is not None
            ):
                raise ValueError("A verification-required receipt needs exact verification IDs")
            return
        if self.outcome == "authorized":
            if (
                any(value is not None for value in verification_ids)
                or self.authorized_delivery_id is None
            ):
                raise ValueError("An authorized receipt needs only its authorized Delivery ID")
            return
        if (
            any(value is not None for value in verification_ids)
            or self.authorized_delivery_id is not None
        ):
            raise ValueError("A rejected receipt cannot contain verification or Delivery IDs")


@dataclass(frozen=True)
class SubmitVerificationCodeInput:
    challenge_id: UUID
    protected_command_id: UUID
    workflow_id: UUID
    thread_id: UUID
    purpose: str
    code: str


@dataclass(frozen=True)
class SubmitVerificationCode:
    command_id: UUID
    actor: Actor
    cause: Cause
    input: SubmitVerificationCodeInput


@dataclass(frozen=True)
class SubmitVerificationCodeResult:
    verification_outcome: VerificationCodeOutcome
    protected_outcome: ProtectedOutcome | None
    challenge_id: UUID
    protected_command_id: UUID
    session_id: UUID | None
    authorized_delivery_id: UUID | None

    def __post_init__(self) -> None:
        if self.verification_outcome == "verified":
            if (
                self.protected_outcome != "authorized"
                or self.session_id is None
                or self.authorized_delivery_id is None
            ):
                raise ValueError("A verified receipt needs assurance and authorized Delivery IDs")
            return
        if self.session_id is not None or self.authorized_delivery_id is not None:
            raise ValueError("A rejected verification receipt cannot contain assurance IDs")
        required_protected_outcome = {
            "identifier_revoked": "identifier_revoked",
            "authority_revoked": "authority_revoked",
            "workflow_closed": "workflow_closed",
        }.get(self.verification_outcome)
        if self.protected_outcome != required_protected_outcome:
            raise ValueError("A rejected verification receipt has inconsistent outcomes")


def _validate_lineage(actor: Actor, cause: Cause) -> None:
    if not actor.identifier.strip() or not cause.identifier.strip():
        raise ValueError("Command Actor and Cause identifiers must be non-empty")


def validate_provision(command: ProvisionVerificationAuthority) -> None:
    _validate_lineage(command.actor, command.cause)
    if command.actor.kind != "system":
        raise ValueError("Verification authority provisioning requires a System Actor")
    if command.input.party_id == command.input.organization_party_id:
        raise ValueError("A Party cannot be its own organization")
    email = command.input.email.strip().casefold()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError("Provisioning requires a canonical email address")


def validate_protected_request(command: RequestProtectedRenewalDetails) -> None:
    _validate_lineage(command.actor, command.cause)
    if command.actor.kind != "party":
        raise ValueError("A protected renewal Command requires a Party Actor")
    try:
        UUID(command.actor.identifier)
    except ValueError as error:
        raise ValueError("Protected Command Party identity must be a UUID") from error
    if not command.input.purpose.strip():
        raise ValueError("Protected Command purpose must be non-empty")


def validate_authority_revocation(command: RevokeVerificationAuthority) -> None:
    _validate_lineage(command.actor, command.cause)
    if command.actor.kind != "system":
        raise ValueError("Verification authority revocation requires a System Actor")


def validate_code_submission(command: SubmitVerificationCode) -> None:
    _validate_lineage(command.actor, command.cause)
    if command.actor.kind != "party":
        raise ValueError("Verification code submission requires a Party Actor")
    try:
        UUID(command.actor.identifier)
    except ValueError as error:
        raise ValueError("Verification Party identity must be a UUID") from error
    if (
        len(command.input.code) != 6
        or not command.input.code.isascii()
        or not command.input.code.isdigit()
    ):
        raise ValueError("Verification code must contain six ASCII digits")
    if not command.input.purpose.strip():
        raise ValueError("Verification purpose must be non-empty")


__all__ = [
    "ProtectedOutcome",
    "ProvisionVerificationAuthority",
    "ProvisionVerificationAuthorityInput",
    "ProvisionVerificationAuthorityResult",
    "RequestProtectedRenewalDetails",
    "RequestProtectedRenewalDetailsInput",
    "RequestProtectedRenewalDetailsResult",
    "RevokeVerificationAuthority",
    "RevokeVerificationAuthorityInput",
    "RevokeVerificationAuthorityResult",
    "SubmitVerificationCode",
    "SubmitVerificationCodeInput",
    "SubmitVerificationCodeResult",
    "VerificationAuthorityTarget",
    "VerificationCodeOutcome",
    "VerificationPurpose",
    "validate_authority_revocation",
    "validate_code_submission",
    "validate_protected_request",
    "validate_provision",
]
