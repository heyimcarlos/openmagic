"""Verification Command registrations and immutable receipt decoders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.commands import CommandRegistryBuilder
from psycopg import Connection

from example_insurance.verification_commands import (
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityResult,
    RequestProtectedRenewalDetails,
    RequestProtectedRenewalDetailsResult,
    RevokeVerificationAuthority,
    RevokeVerificationAuthorityResult,
    SubmitVerificationCode,
    SubmitVerificationCodeResult,
    protected_outcome,
    protected_request_outcome,
    validate_authority_revocation,
    validate_code_submission,
    validate_protected_request,
    validate_provision,
    verification_code_outcome,
)


@dataclass(frozen=True)
class VerificationCommandHandlers:
    provision: Callable[
        [ProvisionVerificationAuthority, Connection[tuple[Any, ...]]],
        ProvisionVerificationAuthorityResult,
    ]
    request: Callable[
        [RequestProtectedRenewalDetails, Connection[tuple[Any, ...]]],
        RequestProtectedRenewalDetailsResult,
    ]
    revoke: Callable[
        [RevokeVerificationAuthority, Connection[tuple[Any, ...]]],
        RevokeVerificationAuthorityResult,
    ]
    submit: Callable[
        [SubmitVerificationCode, Connection[tuple[Any, ...]]],
        SubmitVerificationCodeResult,
    ]


def _optional_uuid(value: object) -> UUID | None:
    return UUID(str(value)) if value is not None else None


def _decode_provision(payload: dict[str, Any]) -> ProvisionVerificationAuthorityResult:
    return ProvisionVerificationAuthorityResult(
        outcome=payload["outcome"],
        party_id=UUID(payload["party_id"]),
        identifier_id=UUID(payload["identifier_id"]),
        membership_id=UUID(payload["membership_id"]),
        participant_id=UUID(payload["participant_id"]),
    )


def _decode_request(payload: dict[str, Any]) -> RequestProtectedRenewalDetailsResult:
    return RequestProtectedRenewalDetailsResult(
        outcome=protected_request_outcome(payload["outcome"]),
        workflow_id=UUID(payload["workflow_id"]),
        challenge_id=_optional_uuid(payload["challenge_id"]),
        verification_workflow_id=_optional_uuid(payload["verification_workflow_id"]),
        verification_instance_id=_optional_uuid(payload["verification_instance_id"]),
        authorized_delivery_id=_optional_uuid(payload["authorized_delivery_id"]),
    )


def _decode_revocation(payload: dict[str, Any]) -> RevokeVerificationAuthorityResult:
    return RevokeVerificationAuthorityResult(
        outcome=payload["outcome"],
        party_id=UUID(payload["party_id"]),
        workflow_id=UUID(payload["workflow_id"]),
        target=payload["target"],
    )


def _decode_submission(payload: dict[str, Any]) -> SubmitVerificationCodeResult:
    return SubmitVerificationCodeResult(
        verification_outcome=verification_code_outcome(payload["verification_outcome"]),
        protected_outcome=protected_outcome(payload["protected_outcome"]),
        challenge_id=UUID(payload["challenge_id"]),
        protected_command_id=UUID(payload["protected_command_id"]),
        session_id=_optional_uuid(payload["session_id"]),
        authorized_delivery_id=_optional_uuid(payload["authorized_delivery_id"]),
    )


def register_verification_commands(
    builder: CommandRegistryBuilder, handlers: VerificationCommandHandlers
) -> CommandRegistryBuilder:
    return (
        builder.register(
            command_type="verification.provision_authority",
            schema_version=1,
            command_class=ProvisionVerificationAuthority,
            result_class=ProvisionVerificationAuthorityResult,
            handler=handlers.provision,
            result_decoder=_decode_provision,
            validator=validate_provision,
        )
        .register(
            command_type="verification.revoke_authority",
            schema_version=1,
            command_class=RevokeVerificationAuthority,
            result_class=RevokeVerificationAuthorityResult,
            handler=handlers.revoke,
            result_decoder=_decode_revocation,
            validator=validate_authority_revocation,
        )
        .register(
            command_type="renewal.read_approved_details",
            schema_version=1,
            command_class=RequestProtectedRenewalDetails,
            result_class=RequestProtectedRenewalDetailsResult,
            handler=handlers.request,
            result_decoder=_decode_request,
            validator=validate_protected_request,
        )
        .register(
            command_type="verification.submit_code",
            schema_version=1,
            command_class=SubmitVerificationCode,
            result_class=SubmitVerificationCodeResult,
            handler=handlers.submit,
            result_decoder=_decode_submission,
            validator=validate_code_submission,
        )
    )


__all__ = ["VerificationCommandHandlers", "register_verification_commands"]
