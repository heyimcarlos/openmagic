"""Application-owned step-up verification control and protected Command policy."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from openmagic_runtime.commands import Actor, Cause, StateConflict
from openmagic_runtime.delivery import DeliveryControl
from openmagic_runtime.kernel.control import KernelControl, StartInstance
from openmagic_runtime.kernel.records import lock_instance
from psycopg import Connection

from example_insurance.renewal_records import record_event
from example_insurance.renewal_workflow_records import lock_instance_for_workflow
from example_insurance.verification_authority_records import (
    lock_authority,
    provision_authority,
    revoke_authority,
)
from example_insurance.verification_challenge_records import (
    DurableProtectedCommand,
    active_session,
    challenge_delivery_confirmed,
    challenge_is_expired,
    establish_session,
    expire_challenge,
    lock_challenge_and_command,
    pending_challenge,
    read_challenge_identity,
    record_authorized_command,
    record_challenge,
    record_failed_code,
    renewal_details,
    resolve_protected_command,
)
from example_insurance.verification_codes import VerificationCodes
from example_insurance.verification_commands import (
    ProtectedOutcome,
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityResult,
    RequestProtectedRenewalDetails,
    RequestProtectedRenewalDetailsResult,
    RevokeVerificationAuthority,
    RevokeVerificationAuthorityResult,
    SubmitVerificationCode,
    SubmitVerificationCodeResult,
    VerificationCodeOutcome,
)
from example_insurance.verification_definition import VERIFICATION_DEFINITION
from example_insurance.verification_policy import (
    VERIFICATION_DELIVERY_RETRY_POLICY,
    VerificationPolicy,
)


class VerificationControl:
    def __init__(
        self,
        *,
        codes: VerificationCodes,
        challenge_ttl_seconds: int = 600,
        session_ttl_seconds: int = 900,
    ) -> None:
        if challenge_ttl_seconds <= 0 or session_ttl_seconds <= 0:
            raise ValueError("Verification expiry durations must be positive")
        self._codes = codes
        self._challenge_ttl_seconds = challenge_ttl_seconds
        self._session_ttl_seconds = session_ttl_seconds
        self._policy = VerificationPolicy()

    @property
    def codes(self) -> VerificationCodes:
        return self._codes

    def provision(
        self,
        command: ProvisionVerificationAuthority,
        connection: Connection[tuple[Any, ...]],
    ) -> ProvisionVerificationAuthorityResult:
        identity = lock_instance_for_workflow(connection, command.input.workflow_id)
        if identity is None:
            raise StateConflict("Exact protected Workflow does not exist")
        provisioned = provision_authority(connection, command.input)
        return ProvisionVerificationAuthorityResult(
            outcome="provisioned",
            party_id=command.input.party_id,
            identifier_id=provisioned.identifier_id,
            membership_id=provisioned.membership_id,
            participant_id=provisioned.participant_id,
        )

    def request(
        self,
        command: RequestProtectedRenewalDetails,
        connection: Connection[tuple[Any, ...]],
    ) -> RequestProtectedRenewalDetailsResult:
        value = command.input
        party_id = UUID(command.actor.identifier)
        identity = lock_instance_for_workflow(connection, value.workflow_id)
        if identity is None:
            return self._request_result(command, "workflow_closed")
        authority = lock_authority(
            connection,
            workflow_id=value.workflow_id,
            party_id=party_id,
            approval_grant_id=value.approval_grant_id,
        )
        if authority is None:
            return self._request_result(command, "workflow_closed")
        outcome = self._policy.authorize(
            authority,
            party_id=party_id,
            thread_id=value.thread_id,
            purpose=value.purpose,
        )
        if outcome != "authorized":
            return self._request_result(command, outcome)
        session_id = active_session(
            connection,
            party_id=party_id,
            thread_id=value.thread_id,
        )
        if session_id is not None:
            delivery_id = self._create_authorized_delivery(
                connection,
                protected_command_id=command.command_id,
                party_id=party_id,
                workflow_id=value.workflow_id,
                thread_id=value.thread_id,
                purpose=value.purpose,
                approval_grant_id=value.approval_grant_id,
                persist_new_command=True,
            )
            return RequestProtectedRenewalDetailsResult(
                outcome="authorized",
                workflow_id=value.workflow_id,
                challenge_id=None,
                verification_workflow_id=None,
                verification_instance_id=None,
                authorized_delivery_id=delivery_id,
            )
        if authority.identifier_id is None:
            return self._request_result(command, "identifier_revoked")
        pending = pending_challenge(connection, party_id=party_id, thread_id=value.thread_id)
        if pending is not None:
            return RequestProtectedRenewalDetailsResult(
                outcome="verification_in_progress",
                workflow_id=value.workflow_id,
                challenge_id=pending.challenge_id,
                verification_workflow_id=pending.delivery_workflow_id,
                verification_instance_id=pending.delivery_instance_id,
                authorized_delivery_id=None,
            )
        challenge_id = uuid4()
        verification_workflow_id = uuid4()
        started = KernelControl(connection).start(
            StartInstance(
                command_id=command.command_id,
                definition_key=VERIFICATION_DEFINITION.identity.key,
                definition_version=VERIFICATION_DEFINITION.identity.version,
                instance_input={
                    "workflow_id": str(verification_workflow_id),
                    "challenge_id": str(challenge_id),
                    "protected_workflow_id": str(value.workflow_id),
                    "thread_id": str(value.thread_id),
                },
                route_input={
                    "challenge_id": str(challenge_id),
                    "protected_workflow_id": str(value.workflow_id),
                    "thread_id": str(value.thread_id),
                },
            )
        )
        record_challenge(
            connection,
            protected_command_id=command.command_id,
            party_id=party_id,
            workflow_id=value.workflow_id,
            thread_id=value.thread_id,
            purpose=value.purpose,
            approval_grant_id=value.approval_grant_id,
            challenge_id=challenge_id,
            destination_identifier_id=authority.identifier_id,
            delivery_workflow_id=verification_workflow_id,
            delivery_instance_id=started.instance_id,
            challenge_ttl_seconds=self._challenge_ttl_seconds,
        )
        return RequestProtectedRenewalDetailsResult(
            outcome="verification_required",
            workflow_id=value.workflow_id,
            challenge_id=challenge_id,
            verification_workflow_id=verification_workflow_id,
            verification_instance_id=started.instance_id,
            authorized_delivery_id=None,
        )

    def revoke(
        self,
        command: RevokeVerificationAuthority,
        connection: Connection[tuple[Any, ...]],
    ) -> RevokeVerificationAuthorityResult:
        value = command.input
        if lock_instance_for_workflow(connection, value.workflow_id) is None:
            raise StateConflict("Exact protected Workflow does not exist")
        revoked = revoke_authority(
            connection,
            party_id=value.party_id,
            workflow_id=value.workflow_id,
            target=value.target,
        )
        record_event(
            connection,
            event_type="verification.authority.revoked",
            workflow_id=value.workflow_id,
            actor=command.actor,
            cause=command.cause,
            payload={"party_id": str(value.party_id), "target": value.target},
        )
        return RevokeVerificationAuthorityResult(
            outcome="revoked" if revoked else "already_revoked",
            party_id=value.party_id,
            workflow_id=value.workflow_id,
            target=value.target,
        )

    def submit(
        self,
        command: SubmitVerificationCode,
        connection: Connection[tuple[Any, ...]],
    ) -> SubmitVerificationCodeResult:
        value = command.input
        code_matches = self._codes.accepts(value.challenge_id, value.code)
        identity = read_challenge_identity(connection, value.challenge_id)
        if identity is None:
            return self._submission_result(command, "invalid_code")
        delivery_instance_id, protected_workflow_id = identity
        if lock_instance(connection, delivery_instance_id) is None:
            return self._submission_result(command, "invalid_code")
        protected_identity = lock_instance_for_workflow(connection, protected_workflow_id)
        if protected_identity is None:
            return self._submission_result(command, "workflow_closed")
        locked = lock_challenge_and_command(connection, value.challenge_id)
        if locked is None:
            return self._submission_result(command, "invalid_code")
        challenge, protected = locked
        binding_outcome = self._binding_outcome(command, protected)
        if binding_outcome is not None:
            return self._submission_result(command, binding_outcome)
        if challenge.state != "pending":
            return self._submission_result(command, "already_used")
        if challenge_is_expired(connection, challenge):
            expire_challenge(connection, challenge.challenge_id)
            return self._submission_result(command, "expired")
        if not challenge_delivery_confirmed(connection, challenge.challenge_id):
            return self._submission_result(command, "delivery_unconfirmed")
        if not code_matches:
            record_failed_code(connection, challenge.challenge_id)
            return self._submission_result(command, "invalid_code")
        party_id = UUID(command.actor.identifier)
        authority = lock_authority(
            connection,
            workflow_id=protected.workflow_id,
            party_id=party_id,
            approval_grant_id=protected.approval_grant_id,
        )
        if authority is None:
            resolve_protected_command(
                connection,
                protected_command_id=protected.protected_command_id,
                outcome="workflow_closed",
                delivery_id=None,
            )
            return self._submission_result(command, "workflow_closed", "workflow_closed")
        protected_outcome = self._policy.authorize(
            authority,
            party_id=party_id,
            thread_id=protected.thread_id,
            purpose=protected.purpose,
        )
        if protected_outcome != "authorized":
            resolve_protected_command(
                connection,
                protected_command_id=protected.protected_command_id,
                outcome=protected_outcome,
                delivery_id=None,
            )
            return self._submission_result(
                command,
                self._verification_rejection(protected_outcome),
                protected_outcome,
            )
        session_id = establish_session(
            connection,
            challenge=challenge,
            session_ttl_seconds=self._session_ttl_seconds,
        )
        delivery_id = self._create_authorized_delivery(
            connection,
            protected_command_id=protected.protected_command_id,
            party_id=protected.party_id,
            workflow_id=protected.workflow_id,
            thread_id=protected.thread_id,
            purpose=protected.purpose,
            approval_grant_id=protected.approval_grant_id,
            persist_new_command=False,
        )
        return SubmitVerificationCodeResult(
            verification_outcome="verified",
            protected_outcome="authorized",
            challenge_id=value.challenge_id,
            protected_command_id=value.protected_command_id,
            session_id=session_id,
            authorized_delivery_id=delivery_id,
        )

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
    def _verification_rejection(outcome: ProtectedOutcome) -> VerificationCodeOutcome:
        if outcome == "identifier_revoked":
            return "identifier_revoked"
        if outcome == "workflow_closed":
            return "workflow_closed"
        return "authority_revoked"

    @staticmethod
    def _request_result(
        command: RequestProtectedRenewalDetails, outcome: ProtectedOutcome
    ) -> RequestProtectedRenewalDetailsResult:
        return RequestProtectedRenewalDetailsResult(
            outcome=outcome,
            workflow_id=command.input.workflow_id,
            challenge_id=None,
            verification_workflow_id=None,
            verification_instance_id=None,
            authorized_delivery_id=None,
        )

    @staticmethod
    def _submission_result(
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

    @staticmethod
    def _create_authorized_delivery(
        connection: Connection[tuple[Any, ...]],
        *,
        protected_command_id: UUID,
        party_id: UUID,
        workflow_id: UUID,
        thread_id: UUID,
        purpose: str,
        approval_grant_id: UUID,
        persist_new_command: bool,
    ) -> UUID:
        details = renewal_details(connection, workflow_id)
        event_id = record_event(
            connection,
            event_type="renewal.protected_details.authorized",
            workflow_id=workflow_id,
            actor=Actor("system", "verification-policy"),
            cause=Cause("command", str(protected_command_id)),
            payload={
                "protected_command_id": str(protected_command_id),
                "approval_grant_id": str(approval_grant_id),
            },
        )
        intent = DeliveryControl(connection).create(
            domain_event_id=event_id,
            thread_id=thread_id,
            audience={"kind": "party", "identifier": str(party_id)},
            message_author={"kind": "system", "identifier": "example-insurance"},
            content_descriptor={
                "template_key": "example_insurance.renewal_protected_details.v1",
                "template_version": 1,
                "purpose": purpose,
            },
            message_content=(
                f"Approved renewal details for policy {details.policy_number}: "
                f"{details.policyholder_name}, renewal date {details.renewal_date}."
            ),
            retry_policy=VERIFICATION_DELIVERY_RETRY_POLICY,
        )
        if persist_new_command:
            record_authorized_command(
                connection,
                protected_command_id=protected_command_id,
                party_id=party_id,
                workflow_id=workflow_id,
                thread_id=thread_id,
                purpose=purpose,
                approval_grant_id=approval_grant_id,
                delivery_id=intent.delivery_id,
            )
        else:
            resolve_protected_command(
                connection,
                protected_command_id=protected_command_id,
                outcome="authorized",
                delivery_id=intent.delivery_id,
            )
        return intent.delivery_id


__all__ = ["VerificationControl"]
