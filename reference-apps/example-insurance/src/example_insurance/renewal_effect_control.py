"""Fenced renewal email dispatch, observations, and reconciliation."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from openmagic_runtime.commands import StateConflict
from openmagic_runtime.kernel.control import KernelControl
from openmagic_runtime.kernel.transitions import GuardCurrentAttempt, ResolveDeferredStep
from openmagic_runtime.kernel.work import ClaimedAttempt, DispositionRequired
from psycopg import Connection

from example_insurance.renewal_completion import RenewalCompletionControl
from example_insurance.renewal_effect_policy import (
    DispatchAuthority,
    EffectObservation,
    RenewalExternalEffectPolicy,
    effect_observation,
)
from example_insurance.renewal_effect_records import (
    commit_dispatch_fence,
    load_durable_dispatch_claim,
    lock_approval_grant,
    lock_external_effect,
    lock_reconciliation_target,
    lock_workflow_authority,
    record_effect_observation,
    requested_dispatch_claim,
)
from example_insurance.renewal_effects import (
    ExternalEffectPermit,
    logical_effect_id,
)
from example_insurance.renewal_records import (
    CommandEventLineage,
    EffectEvidenceSource,
    record_event,
)


class RenewalEffectControl:
    def __init__(self) -> None:
        self._policy = RenewalExternalEffectPolicy()
        self._completion = RenewalCompletionControl()

    def authorize_dispatch(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        lineage: CommandEventLineage,
    ) -> ExternalEffectPermit:
        requested = requested_dispatch_claim(attempt, worker_id)
        workflow = lock_workflow_authority(connection, attempt.instance_id)
        guard = KernelControl(connection).guard_current_attempt(
            GuardCurrentAttempt(
                instance_id=attempt.instance_id,
                step_id=attempt.step_id,
                attempt_id=attempt.attempt_id,
                attempt_number=attempt.attempt_number,
            )
        )
        grant = lock_approval_grant(connection, workflow.workflow_id, attempt.step_id)
        effect = lock_external_effect(connection, attempt.step_id)
        self._policy.authorize_dispatch(
            authority=DispatchAuthority(
                workflow=workflow,
                grant=grant,
                requested_claim=requested,
                durable_claim=load_durable_dispatch_claim(connection, attempt.attempt_id),
                expected_logical_effect_id=logical_effect_id(attempt.step_id),
                effect=effect,
            )
        )
        guard.require_usable()
        effect_id = logical_effect_id(attempt.step_id)
        provider_key = commit_dispatch_fence(
            connection,
            workflow=workflow,
            grant=grant,
            claim=requested,
            effect=effect,
            effect_id=effect_id,
        )
        record_event(
            connection,
            event_type="external_effect.dispatch_started",
            workflow_id=workflow.workflow_id,
            actor=lineage.actor,
            cause=lineage.cause,
            payload={
                "logical_effect_id": str(effect_id),
                "step_id": str(attempt.step_id),
                "attempt_id": str(attempt.attempt_id),
                "attempt_number": attempt.attempt_number,
            },
        )
        return ExternalEffectPermit(
            logical_effect_id=effect_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            provider_idempotency_key=provider_key,
            effect_fingerprint=grant.effect_fingerprint,
            effect=requested.effect,
        )

    def recover_fenced_attempt(
        self,
        connection: Connection[tuple[Any, ...]],
        required: DispositionRequired,
    ) -> tuple[dict[str, UUID], dict[str, UUID]] | None:
        if required.template_key != "send_renewal_email":
            return None
        effect = lock_external_effect(connection, required.step_id)
        control = KernelControl(connection)
        if effect is None:
            if required.attempt_number < self._policy.maximum_attempts:
                control.retry(required)
            else:
                control.fail(required, failure={"class": "attempt_budget_exhausted"})
            return {}, {}
        record_effect_observation(
            connection,
            logical_effect_id=effect.logical_effect_id,
            attempt_id=required.attempt_id,
            classification="uncertain",
            source="worker_loss_after_fence",
            provider_request_id=None,
        )
        return control.defer(
            required,
            outcome_route="reconcile_email",
            route_input={
                "workflow_id": str(effect.workflow_id),
                "effect_step_id": str(required.step_id),
                "basis_attempt_id": str(required.attempt_id),
                "logical_effect_id": str(effect.logical_effect_id),
                "provider_idempotency_key": effect.provider_idempotency_key,
            },
        )

    def accept_email_observation(
        self,
        connection: Connection[tuple[Any, ...]],
        required: DispositionRequired,
        lineage: CommandEventLineage,
    ) -> tuple[dict[str, UUID], dict[str, UUID]]:
        effect = lock_external_effect(connection, required.step_id)
        if effect is None or effect.dispatch_attempt_id != required.attempt_id:
            raise StateConflict("Attempt observation has no matching dispatch fence")
        classification = effect_observation(required.observation["classification"])
        self._record_observation(
            connection,
            effect_id=effect.logical_effect_id,
            required=required,
            classification=classification,
            source="provider_response",
            lineage=lineage,
        )
        disposition = self._policy.result_disposition(
            classification=classification,
            attempt_number=required.attempt_number,
            maximum_attempts=self._policy.maximum_attempts,
        )
        control = KernelControl(connection)
        if disposition == "succeed":
            result = control.succeed(
                required,
                output={
                    "logical_effect_id": str(effect.logical_effect_id),
                    "classification": "applied",
                },
            )
            self._completion.complete_if_ready(connection, effect.workflow_id, lineage)
            return result
        if disposition == "retry":
            control.retry(required)
            return {}, {}
        if disposition == "fail":
            control.fail(
                required,
                failure={"class": "external_effect_attempt_budget_exhausted"},
            )
            return {}, {}
        return control.defer(
            required,
            outcome_route="reconcile_email",
            route_input={
                "workflow_id": str(effect.workflow_id),
                "effect_step_id": str(required.step_id),
                "basis_attempt_id": str(required.attempt_id),
                "logical_effect_id": str(effect.logical_effect_id),
                "provider_idempotency_key": effect.provider_idempotency_key,
            },
        )

    def accept_reconciliation_observation(
        self,
        connection: Connection[tuple[Any, ...]],
        required: DispositionRequired,
        lineage: CommandEventLineage,
    ) -> tuple[dict[str, UUID], dict[str, UUID]]:
        target = lock_reconciliation_target(connection, required.step_id)
        classification = effect_observation(required.observation["classification"])
        self._record_observation(
            connection,
            effect_id=target.effect.logical_effect_id,
            required=required,
            classification=classification,
            source="provider_lookup",
            lineage=lineage,
        )
        disposition = self._policy.reconciliation_disposition(
            classification=classification,
            effect_attempt_number=target.effect_attempt_number,
            reconciliation_attempt_number=required.attempt_number,
            maximum_effect_attempts=self._policy.maximum_attempts,
            maximum_reconciliation_attempts=self._policy.maximum_attempts,
        )
        control = KernelControl(connection)
        if disposition == "retry_reconciliation":
            control.retry(required)
            return {}, {}
        if disposition == "defer":
            return control.defer(required)
        control.succeed(
            required,
            output={
                "logical_effect_id": str(target.effect.logical_effect_id),
                "classification": classification,
            },
        )
        if disposition == "confirm":
            control.resolve_deferred(
                ResolveDeferredStep(
                    source_id=required.attempt_id,
                    instance_id=required.instance_id,
                    step_id=target.effect_step_id,
                    basis_attempt_id=target.basis_attempt_id,
                    action="succeed",
                    output={
                        "logical_effect_id": str(target.effect.logical_effect_id),
                        "classification": "applied",
                    },
                )
            )
            self._completion.complete_if_ready(connection, target.effect.workflow_id, lineage)
        elif disposition == "retry_effect":
            control.resolve_deferred(
                ResolveDeferredStep(
                    source_id=required.attempt_id,
                    instance_id=required.instance_id,
                    step_id=target.effect_step_id,
                    basis_attempt_id=target.basis_attempt_id,
                    action="retry",
                )
            )
        else:
            control.resolve_deferred(
                ResolveDeferredStep(
                    source_id=required.attempt_id,
                    instance_id=required.instance_id,
                    step_id=target.effect_step_id,
                    basis_attempt_id=target.basis_attempt_id,
                    action="fail",
                    failure={"class": "external_effect_attempt_budget_exhausted"},
                )
            )
        return {}, {}

    @staticmethod
    def _record_observation(
        connection: Connection[tuple[Any, ...]],
        *,
        effect_id: UUID,
        required: DispositionRequired,
        classification: EffectObservation,
        source: EffectEvidenceSource,
        lineage: CommandEventLineage,
    ) -> None:
        workflow_id = record_effect_observation(
            connection,
            logical_effect_id=effect_id,
            attempt_id=required.attempt_id,
            classification=classification,
            source=source,
            provider_request_id=str(required.observation["provider_request_id"]),
        )
        record_event(
            connection,
            event_type=f"external_effect.{classification}",
            workflow_id=workflow_id,
            actor=lineage.actor,
            cause=lineage.cause,
            payload={
                "logical_effect_id": str(effect_id),
                "attempt_id": str(required.attempt_id),
                "classification": classification,
            },
        )


__all__ = ["RenewalEffectControl"]
