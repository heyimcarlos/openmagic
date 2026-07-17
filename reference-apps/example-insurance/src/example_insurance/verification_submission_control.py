"""Application Policy for verification code acceptance and protected resumption."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from openmagic_runtime.kernel.inspection import KernelTransactionInspection
from psycopg import Connection

from example_insurance._persistence.renewal_workflow_records import lock_instance_for_workflow
from example_insurance._persistence.verification_authority_records import lock_authority
from example_insurance._persistence.verification_challenge_records import (
    DurableChallenge,
    DurableProtectedCommand,
    challenge_is_expired,
    establish_session,
    lock_challenge_and_command,
    read_challenge_identity,
    record_failed_code,
    resolve_terminal_challenge,
)
from example_insurance.verification_challenge_lifecycle import VerificationChallengeLifecycle
from example_insurance.verification_codes import VerificationCodes
from example_insurance.verification_commands import (
    ProtectedOutcome,
    ProtectedPolicyRejection,
    SubmitVerificationCode,
    SubmitVerificationCodeResult,
    VerificationCodeOutcome,
    protected_policy_rejection,
    verification_rejection,
)
from example_insurance.verification_policy import (
    MAX_FAILED_CODE_ATTEMPTS,
    VerificationPolicy,
)
from example_insurance.verification_protected_delivery import (
    ProtectedDeliveryContext,
    ProtectedRenewalDeliveryControl,
)


class VerificationSubmissionControl:
    def __init__(
        self,
        *,
        codes: VerificationCodes,
        session_ttl_seconds: int,
        policy: VerificationPolicy,
        lifecycle: VerificationChallengeLifecycle,
        deliveries: ProtectedRenewalDeliveryControl,
    ) -> None:
        self._codes = codes
        self._session_ttl_seconds = session_ttl_seconds
        self._policy = policy
        self._lifecycle = lifecycle
        self._deliveries = deliveries

    def submit(
        self,
        command: SubmitVerificationCode,
        connection: Connection[tuple[Any, ...]],
    ) -> SubmitVerificationCodeResult:
        value = command.input
        code_matches = self._codes.accepts(value.challenge_id, value.code)
        identity = read_challenge_identity(connection, value.challenge_id)
        if (
            identity is None
            or KernelTransactionInspection(connection).lock_instance(identity.delivery_instance_id)
            is None
        ):
            return self._result(command, "invalid_code")
        if lock_instance_for_workflow(connection, identity.protected_workflow_id) is None:
            return self._reject_closed_workflow(command, connection)
        locked = lock_challenge_and_command(connection, value.challenge_id)
        if locked is None:
            return self._result(command, "invalid_code")
        challenge, protected = locked
        binding_outcome = self._binding_outcome(command, protected)
        if binding_outcome is not None:
            return self._result(command, binding_outcome)
        if challenge.state == "attempts_exhausted":
            return self._result(command, "invalid_code")
        if challenge.state != "pending":
            return self._terminal_result(command, challenge, protected)
        if challenge_is_expired(connection, challenge):
            resolve_terminal_challenge(
                connection,
                challenge=challenge,
                resolution="verification_expired",
            )
            return self._result(command, "expired")
        delivery_status = self._lifecycle.delivery_status(connection, challenge)
        if delivery_status == "failed":
            resolve_terminal_challenge(
                connection,
                challenge=challenge,
                resolution="verification_delivery_failed",
            )
            return self._result(command, "delivery_failed")
        if delivery_status != "delivered":
            return self._result(command, "delivery_unconfirmed")
        if not code_matches:
            record_failed_code(
                connection,
                challenge,
                maximum_attempts=MAX_FAILED_CODE_ATTEMPTS,
            )
            return self._result(command, "invalid_code")
        party_id = UUID(command.actor.identifier)
        authority = lock_authority(
            connection,
            workflow_id=protected.workflow_id,
            party_id=party_id,
            approval_grant_id=protected.approval_grant_id,
        )
        if authority is None:
            return self._reject(command, connection, challenge, protected, "workflow_closed")
        protected_outcome = self._policy.authorize(
            authority,
            party_id=party_id,
            thread_id=protected.thread_id,
            purpose=protected.purpose,
        )
        if protected_outcome != "authorized":
            return self._reject(
                command,
                connection,
                challenge,
                protected,
                protected_outcome,
            )
        session_id = establish_session(
            connection,
            submit_command_id=command.command_id,
            challenge=challenge,
            session_ttl_seconds=self._session_ttl_seconds,
        )
        delivery_id = self._deliveries.resume_waiting_command(
            connection,
            ProtectedDeliveryContext(
                protected_command_id=protected.protected_command_id,
                party_id=protected.party_id,
                workflow_id=protected.workflow_id,
                thread_id=protected.thread_id,
                purpose=protected.purpose,
                approval_grant_id=protected.approval_grant_id,
            ),
        )
        return SubmitVerificationCodeResult(
            verification_outcome="verified",
            protected_outcome="authorized",
            challenge_id=value.challenge_id,
            protected_command_id=value.protected_command_id,
            session_id=session_id,
            authorized_delivery_id=delivery_id,
        )

    def _reject_closed_workflow(
        self,
        command: SubmitVerificationCode,
        connection: Connection[tuple[Any, ...]],
    ) -> SubmitVerificationCodeResult:
        locked = lock_challenge_and_command(connection, command.input.challenge_id)
        if locked is None:
            return self._result(command, "invalid_code")
        challenge, protected = locked
        binding_outcome = self._binding_outcome(command, protected)
        if binding_outcome is not None:
            return self._result(command, binding_outcome)
        if challenge.state != "pending":
            return self._terminal_result(command, challenge, protected)
        return self._reject(command, connection, challenge, protected, "workflow_closed")

    def _reject(
        self,
        command: SubmitVerificationCode,
        connection: Connection[tuple[Any, ...]],
        challenge: DurableChallenge,
        protected: DurableProtectedCommand,
        protected_outcome: ProtectedPolicyRejection,
    ) -> SubmitVerificationCodeResult:
        resolve_terminal_challenge(
            connection,
            challenge=challenge,
            resolution=protected_outcome,
        )
        return self._result(
            command,
            verification_rejection(protected_outcome),
            protected_outcome,
        )

    def _terminal_result(
        self,
        command: SubmitVerificationCode,
        challenge: DurableChallenge,
        protected: DurableProtectedCommand,
    ) -> SubmitVerificationCodeResult:
        if challenge.state == "expired":
            return self._result(command, "expired")
        if challenge.state == "delivery_failed":
            return self._result(command, "delivery_failed")
        if challenge.state == "rejected":
            protected_outcome = self._terminal_protected_outcome(protected.outcome)
            return self._result(
                command,
                verification_rejection(protected_outcome),
                protected_outcome,
            )
        return self._result(command, "already_used")

    @staticmethod
    def _binding_outcome(
        command: SubmitVerificationCode,
        protected: DurableProtectedCommand,
    ) -> VerificationCodeOutcome | None:
        value = command.input
        if command.actor.identifier != str(protected.party_id):
            return "wrong_party"
        if value.protected_command_id != protected.protected_command_id:
            return "wrong_protected_command"
        if value.workflow_id != protected.workflow_id:
            return "wrong_workflow"
        if value.thread_id != protected.thread_id:
            return "wrong_thread"
        if value.purpose != protected.purpose:
            return "wrong_purpose"
        return None

    @staticmethod
    def _terminal_protected_outcome(value: str | None) -> ProtectedPolicyRejection:
        try:
            return protected_policy_rejection(value)
        except ValueError as error:
            raise RuntimeError(
                "Rejected protected Command has no terminal policy outcome"
            ) from error

    @staticmethod
    def _result(
        command: SubmitVerificationCode,
        verification_outcome: VerificationCodeOutcome,
        protected_outcome: ProtectedOutcome | None = None,
    ) -> SubmitVerificationCodeResult:
        return SubmitVerificationCodeResult(
            verification_outcome=verification_outcome,
            protected_outcome=protected_outcome,
            challenge_id=command.input.challenge_id,
            protected_command_id=command.input.protected_command_id,
            session_id=None,
            authorized_delivery_id=None,
        )


__all__ = ["VerificationSubmissionControl"]
