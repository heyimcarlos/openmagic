"""Application Policy for verification authority and protected requests."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from openmagic_runtime.commands import StateConflict
from openmagic_runtime.kernel.control import KernelControl, StartInstance
from openmagic_runtime.threads import ThreadStore
from psycopg import Connection

from example_insurance.renewal_records import record_event
from example_insurance.renewal_workflow_records import lock_instance_for_workflow
from example_insurance.verification_authority_records import (
    lock_authority,
    provision_authority,
    revoke_authority,
)
from example_insurance.verification_challenge_lifecycle import VerificationChallengeLifecycle
from example_insurance.verification_challenge_records import active_session, record_challenge
from example_insurance.verification_commands import (
    ProtectedOutcome,
    ProtectedPolicyRejection,
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityResult,
    RequestProtectedRenewalDetails,
    RequestProtectedRenewalDetailsResult,
    RevokeVerificationAuthority,
    RevokeVerificationAuthorityResult,
)
from example_insurance.verification_definition import VERIFICATION_DEFINITION
from example_insurance.verification_policy import VerificationPolicy
from example_insurance.verification_protected_delivery import (
    ProtectedDeliveryContext,
    ProtectedRenewalDeliveryControl,
)
from example_insurance.verification_workflow_records import record_verification_workflow


class VerificationRequestControl:
    def __init__(
        self,
        *,
        challenge_ttl_seconds: int,
        policy: VerificationPolicy,
        lifecycle: VerificationChallengeLifecycle,
        deliveries: ProtectedRenewalDeliveryControl,
        threads: ThreadStore,
    ) -> None:
        self._challenge_ttl_seconds = challenge_ttl_seconds
        self._policy = policy
        self._lifecycle = lifecycle
        self._deliveries = deliveries
        self._threads = threads

    def provision(
        self,
        command: ProvisionVerificationAuthority,
        connection: Connection[tuple[Any, ...]],
    ) -> ProvisionVerificationAuthorityResult:
        self._require_identifier_thread(command)
        self._lifecycle.lock_pending_instances(
            connection,
            party_id=command.input.party_id,
            thread_id=None,
            workflow_id=command.input.workflow_id,
        )
        if lock_instance_for_workflow(connection, command.input.workflow_id) is None:
            raise StateConflict("Exact protected Workflow does not exist")
        provisioned = provision_authority(connection, command.input)
        self._lifecycle.reconcile_superseded_identifier(
            connection,
            party_id=command.input.party_id,
            workflow_id=command.input.workflow_id,
            current_identifier_id=provisioned.identifier_id,
        )
        return ProvisionVerificationAuthorityResult(
            outcome="provisioned",
            party_id=command.input.party_id,
            identifier_id=provisioned.identifier_id,
            membership_id=provisioned.membership_id,
            participant_id=provisioned.participant_id,
        )

    def _require_identifier_thread(self, command: ProvisionVerificationAuthority) -> None:
        value = command.input
        try:
            thread = self._threads.read(value.delivery_thread_id)
        except KeyError as error:
            raise ValueError("Verification identifier Thread is unavailable") from error
        if (
            thread.channel_kind != "email"
            or thread.channel_reference.strip().casefold() != value.email.strip().casefold()
        ):
            raise ValueError("Verification identifier must match its exact public email Thread")

    def request(
        self,
        command: RequestProtectedRenewalDetails,
        connection: Connection[tuple[Any, ...]],
    ) -> RequestProtectedRenewalDetailsResult:
        value = command.input
        party_id = UUID(command.actor.identifier)
        self._lifecycle.lock_pending_instances(
            connection,
            party_id=party_id,
            thread_id=value.thread_id,
            workflow_id=value.workflow_id,
        )
        if lock_instance_for_workflow(connection, value.workflow_id) is None:
            return self._terminal_request(command, party_id, connection, "workflow_closed")
        authority = lock_authority(
            connection,
            workflow_id=value.workflow_id,
            party_id=party_id,
            approval_grant_id=value.approval_grant_id,
        )
        if authority is None:
            return self._terminal_request(command, party_id, connection, "workflow_closed")
        outcome = self._policy.authorize(
            authority,
            party_id=party_id,
            thread_id=value.thread_id,
            purpose=value.purpose,
        )
        if outcome != "authorized":
            if outcome in {"authority_revoked", "identifier_revoked", "workflow_closed"}:
                self._lifecycle.reconcile_pending(
                    connection,
                    party_id=party_id,
                    thread_id=value.thread_id,
                    workflow_id=value.workflow_id,
                    terminal_outcome=outcome,
                )
            return self._result(command, outcome)
        self._lifecycle.reconcile_pending(
            connection,
            party_id=party_id,
            thread_id=value.thread_id,
            workflow_id=value.workflow_id,
            terminal_outcome=None,
        )
        if active_session(connection, party_id=party_id, thread_id=value.thread_id) is not None:
            delivery_id = self._deliveries.create_for_new_command(
                connection,
                ProtectedDeliveryContext(
                    protected_command_id=command.command_id,
                    party_id=party_id,
                    workflow_id=value.workflow_id,
                    thread_id=value.thread_id,
                    purpose=value.purpose,
                    approval_grant_id=value.approval_grant_id,
                ),
            )
            return RequestProtectedRenewalDetailsResult(
                outcome="authorized",
                workflow_id=value.workflow_id,
                challenge_id=None,
                verification_workflow_id=None,
                verification_instance_id=None,
                authorized_delivery_id=delivery_id,
            )
        if authority.identifier_id is None or authority.identifier_delivery_thread_id is None:
            return self._result(command, "identifier_revoked")
        return self._start_challenge(
            command,
            connection,
            party_id,
            authority.identifier_id,
            authority.identifier_delivery_thread_id,
        )

    def revoke(
        self,
        command: RevokeVerificationAuthority,
        connection: Connection[tuple[Any, ...]],
    ) -> RevokeVerificationAuthorityResult:
        value = command.input
        self._lifecycle.lock_pending_instances(
            connection,
            party_id=value.party_id,
            thread_id=None,
            workflow_id=value.workflow_id,
        )
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
        self._lifecycle.reconcile_pending(
            connection,
            party_id=value.party_id,
            thread_id=None,
            workflow_id=value.workflow_id,
            terminal_outcome=(
                "identifier_revoked" if value.target == "identifier" else "authority_revoked"
            ),
        )
        return RevokeVerificationAuthorityResult(
            outcome="revoked" if revoked else "already_revoked",
            party_id=value.party_id,
            workflow_id=value.workflow_id,
            target=value.target,
        )

    def _terminal_request(
        self,
        command: RequestProtectedRenewalDetails,
        party_id: UUID,
        connection: Connection[tuple[Any, ...]],
        outcome: ProtectedPolicyRejection,
    ) -> RequestProtectedRenewalDetailsResult:
        self._lifecycle.reconcile_pending(
            connection,
            party_id=party_id,
            thread_id=command.input.thread_id,
            workflow_id=command.input.workflow_id,
            terminal_outcome=outcome,
        )
        return self._result(command, outcome)

    def _start_challenge(
        self,
        command: RequestProtectedRenewalDetails,
        connection: Connection[tuple[Any, ...]],
        party_id: UUID,
        identifier_id: UUID,
        identifier_thread_id: UUID,
    ) -> RequestProtectedRenewalDetailsResult:
        value = command.input
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
                    "destination_thread_id": str(identifier_thread_id),
                },
                route_input={
                    "challenge_id": str(challenge_id),
                    "protected_workflow_id": str(value.workflow_id),
                    "thread_id": str(value.thread_id),
                    "destination_thread_id": str(identifier_thread_id),
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
            destination_identifier_id=identifier_id,
            destination_thread_id=identifier_thread_id,
            delivery_workflow_id=verification_workflow_id,
            delivery_instance_id=started.instance_id,
            challenge_ttl_seconds=self._challenge_ttl_seconds,
        )
        record_verification_workflow(
            connection,
            workflow_id=verification_workflow_id,
            instance_id=started.instance_id,
            challenge_id=challenge_id,
            protected_workflow_id=value.workflow_id,
        )
        return RequestProtectedRenewalDetailsResult(
            outcome="verification_required",
            workflow_id=value.workflow_id,
            challenge_id=challenge_id,
            verification_workflow_id=verification_workflow_id,
            verification_instance_id=started.instance_id,
            authorized_delivery_id=None,
        )

    @staticmethod
    def _result(
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


__all__ = ["VerificationRequestControl"]
