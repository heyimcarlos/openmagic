"""Verification delivery Attempt acceptance above the generic kernel."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid5

from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.delivery import DeliveryControl
from openmagic_runtime.kernel.control import KernelControl
from openmagic_runtime.kernel.transitions import CloseInstance
from openmagic_runtime.kernel.work import ClaimedAttempt, DispositionRequired, KernelWork
from psycopg import Connection

from example_insurance.renewal_commands import WorkflowAttemptResult
from example_insurance.verification_authority_records import lock_identifier_destination
from example_insurance.verification_challenge_records import (
    challenge_is_expired,
    lock_challenge,
    resolve_terminal_challenge,
)
from example_insurance.verification_codes import VerificationCodes
from example_insurance.verification_policy import (
    VERIFICATION_ATTEMPT_RETRY_POLICY,
    VERIFICATION_DELIVERY_RETRY_POLICY,
)
from example_insurance.verification_workflow_records import (
    complete_verification_workflow,
    expired_verification_instances,
    fail_verification_workflow,
    lock_verification_attempt,
    record_verification_event,
)

_CLOSURE_NAMESPACE = UUID("64d018be-17fb-4970-9c1c-9e00d4fa504d")


class VerificationAttemptControl:
    template_key = "deliver_verification_challenge"

    def __init__(self, *, codes: VerificationCodes) -> None:
        self._codes = codes

    def accept_observation(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        required = KernelWork(connection).accept_result(
            attempt,
            worker_id=worker_id,
            observation=observation,
        )
        workflow = lock_verification_attempt(connection, required.instance_id)
        challenge = lock_challenge(connection, workflow.challenge_id)
        if observation != {"challenge_id": str(challenge.challenge_id)}:
            raise RuntimeError("Verification delivery observation conflicts with its Challenge")
        if workflow.lifecycle == "completed":
            return self._result(attempt)
        if challenge.state != "pending":
            self._fail_and_close(
                connection,
                required=required,
                workflow_id=workflow.workflow_id,
                failure_class="verification_challenge_terminal",
            )
            return self._result(attempt)
        if challenge_is_expired(connection, challenge):
            self._fail_and_close(
                connection,
                required=required,
                workflow_id=workflow.workflow_id,
                failure_class="verification_challenge_expired",
            )
            resolve_terminal_challenge(
                connection,
                challenge=challenge,
                resolution="verification_expired",
            )
            return self._result(attempt)
        destination = lock_identifier_destination(connection, party_id=challenge.party_id)
        if (
            destination is None
            or destination.identifier_id != challenge.destination_identifier_id
            or destination.party_id != challenge.party_id
            or destination.delivery_thread_id != challenge.destination_thread_id
        ):
            self._fail_and_close(
                connection,
                required=required,
                workflow_id=workflow.workflow_id,
                failure_class="verification_identifier_unavailable",
            )
            resolve_terminal_challenge(
                connection,
                challenge=challenge,
                resolution="identifier_revoked",
            )
            return self._result(attempt)
        event_id = record_verification_event(
            connection,
            workflow_id=workflow.workflow_id,
            actor=Actor("system", "verification-control-plane"),
            cause=Cause("attempt", str(attempt.attempt_id)),
            payload={
                "challenge_id": str(challenge.challenge_id),
                "verification_workflow_id": str(workflow.workflow_id),
            },
        )
        code = self._codes.derive(challenge.challenge_id)
        delivery = DeliveryControl(connection).create(
            domain_event_id=event_id,
            thread_id=challenge.destination_thread_id,
            audience={"kind": "party", "identifier": str(challenge.party_id)},
            message_author={"kind": "system", "identifier": "example-insurance"},
            content_descriptor={
                "template_key": "example_insurance.verification_code.v1",
                "template_version": 1,
                "locale": "en-CA",
                "input": {"challenge_id": str(challenge.challenge_id)},
            },
            message_content=f"Your Example Insurance verification code is {code}.",
            retry_policy=VERIFICATION_DELIVERY_RETRY_POLICY,
        )
        KernelControl(connection).succeed(
            required,
            output={"challenge_id": str(challenge.challenge_id)},
            outcome_route=None,
            route_input=None,
        )
        KernelControl(connection).close(
            CloseInstance(
                command_id=uuid5(_CLOSURE_NAMESPACE, str(attempt.attempt_id)),
                instance_id=required.instance_id,
            )
        )
        complete_verification_workflow(
            connection,
            workflow_id=workflow.workflow_id,
            event_id=event_id,
            delivery_id=delivery.delivery_id,
        )
        return self._result(attempt)

    def recover_expired(self, connection: Connection[tuple[Any, ...]]) -> bool:
        work = KernelWork(connection)
        for instance_id in expired_verification_instances(connection):
            required = work.recover_expired(instance_id)
            if required is None:
                continue
            workflow = lock_verification_attempt(connection, required.instance_id)
            challenge = lock_challenge(connection, workflow.challenge_id)
            if required.attempt_number < VERIFICATION_ATTEMPT_RETRY_POLICY.max_attempts:
                KernelControl(connection).retry(required)
            else:
                self._fail_and_close(
                    connection,
                    required=required,
                    workflow_id=workflow.workflow_id,
                    failure_class="verification_delivery_attempts_exhausted",
                )
                resolve_terminal_challenge(
                    connection,
                    challenge=challenge,
                    resolution="verification_delivery_failed",
                )
            return True
        return False

    @staticmethod
    def _fail_and_close(
        connection: Connection[tuple[Any, ...]],
        *,
        required: DispositionRequired,
        workflow_id: UUID,
        failure_class: str,
    ) -> None:
        control = KernelControl(connection)
        control.fail(required, failure={"class": failure_class})
        control.close(
            CloseInstance(
                command_id=uuid5(_CLOSURE_NAMESPACE, str(required.attempt_id)),
                instance_id=required.instance_id,
            )
        )
        fail_verification_workflow(connection, workflow_id=workflow_id)

    @staticmethod
    def _result(attempt: ClaimedAttempt) -> WorkflowAttemptResult:
        return WorkflowAttemptResult(
            attempt_id=attempt.attempt_id,
            template_key=attempt.template_key,
            executor_key=attempt.executor_key,
            agent_run_id=None,
            agent_runtime_generation=None,
            steps={},
            waits={},
        )


__all__ = ["VerificationAttemptControl"]
