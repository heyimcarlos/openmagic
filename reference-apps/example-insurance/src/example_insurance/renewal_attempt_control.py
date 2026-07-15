"""Renewal Workflow Attempt recovery and accepted observation transitions."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from openmagic_runtime.agents import AgentRuns
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.delivery import DeliveryControl
from openmagic_runtime.evidence import content_fingerprint
from openmagic_runtime.kernel.control import KernelControl
from openmagic_runtime.kernel.work import ClaimedAttempt, DispositionRequired, KernelWork
from psycopg import Connection

from example_insurance.renewal_commands import WorkflowAttemptResult
from example_insurance.renewal_effect_control import RenewalEffectControl
from example_insurance.renewal_effects import RenewalEmailEffect
from example_insurance.renewal_policies import RenewalDeliveryPolicy, RenewalWorkflowPolicy
from example_insurance.renewal_records import CommandEventLineage, record_event
from example_insurance.renewal_workflow_records import (
    activation_receipt,
    load_draft_for_step,
    lock_next_expired_workflow_instance,
    lock_workflow_for_attempt,
    record_draft,
)


class RenewalAttemptControl:
    def __init__(self, *, effect_control: RenewalEffectControl) -> None:
        self._effect_control = effect_control
        self._workflow_policy = RenewalWorkflowPolicy()
        self._delivery_policy = RenewalDeliveryPolicy()

    def recover_expired(self, connection: Connection[tuple[Any, ...]]) -> bool:
        instance_id = lock_next_expired_workflow_instance(connection)
        if instance_id is None:
            return False
        required = KernelWork(connection).recover_expired(instance_id)
        if required is None:
            return False
        AgentRuns(connection).abandon_for_attempt(required.attempt_id)
        effect_recovery = self._effect_control.recover_fenced_attempt(connection, required)
        if effect_recovery is not None:
            if not required.consumed:
                raise RuntimeError("External Effect recovery remained unresolved")
            return True
        decision = self._workflow_policy.expired_attempt(
            template_key=required.template_key,
            attempt_number=required.attempt_number,
        )
        control = KernelControl(connection)
        if decision.action == "retry":
            control.retry(required)
        else:
            control.fail(required, failure=decision.failure)
        if not required.consumed:
            raise RuntimeError("Recovery disposition remained unresolved")
        return True

    def accept_observation(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
    ) -> WorkflowAttemptResult:
        if attempt.template_key in {"send_renewal_email", "reconcile_renewal_email"}:
            raise RuntimeError("External Effect observations require committed Command lineage")
        return self._accept_observation(
            connection,
            attempt=attempt,
            worker_id=worker_id,
            observation=observation,
            effect_lineage=None,
        )

    def accept_effect_observation(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
        lineage: CommandEventLineage,
    ) -> WorkflowAttemptResult:
        if attempt.template_key not in {"send_renewal_email", "reconcile_renewal_email"}:
            raise RuntimeError("Command observation does not target an External Effect Step")
        return self._accept_observation(
            connection,
            attempt=attempt,
            worker_id=worker_id,
            observation=observation,
            effect_lineage=lineage,
        )

    def _accept_observation(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
        effect_lineage: CommandEventLineage | None,
    ) -> WorkflowAttemptResult:
        workflow = lock_workflow_for_attempt(connection, attempt.instance_id)
        required = KernelWork(connection).accept_result(
            attempt,
            worker_id=worker_id,
            observation=observation,
        )
        if required.replayed and required.template_key in {
            "send_renewal_email",
            "reconcile_renewal_email",
        }:
            activation = activation_receipt(
                connection,
                instance_id=required.instance_id,
                source_attempt_id=required.attempt_id,
            )
            return WorkflowAttemptResult(
                attempt_id=required.attempt_id,
                template_key=required.template_key,
                executor_key=attempt.executor_key,
                agent_run_id=None,
                agent_runtime_generation=None,
                steps=activation.steps,
                waits=activation.waits,
            )

        agent_run_id: UUID | None = None
        if required.template_key == "gather_renewal_facts":
            decision = self._workflow_policy.facts_succeeded(
                workflow_id=workflow.workflow_id,
                thread_id=workflow.thread_id,
                observation=observation,
            )
            steps, waits = KernelControl(connection).succeed(
                required,
                output=decision.output,
                outcome_route=decision.outcome_route,
                route_input=decision.route_input,
            )
        elif required.template_key == "draft_renewal_email":
            steps, waits, agent_run_id = self._accept_draft(
                connection,
                attempt=attempt,
                required=required,
                workflow_id=workflow.workflow_id,
                thread_id=workflow.thread_id,
                observation=observation,
            )
        elif required.template_key == "send_renewal_email":
            if effect_lineage is None:
                raise RuntimeError("Email effect observation lacks Command lineage")
            steps, waits = self._effect_control.accept_email_observation(
                connection,
                required,
                effect_lineage,
            )
        elif required.template_key == "reconcile_renewal_email":
            if effect_lineage is None:
                raise RuntimeError("Reconciliation observation lacks Command lineage")
            steps, waits = self._effect_control.accept_reconciliation_observation(
                connection,
                required,
                effect_lineage,
            )
        else:
            raise RuntimeError(f"unsupported renewal Step: {required.template_key}")
        if not required.consumed:
            raise RuntimeError("Attempt disposition remained unresolved")
        return WorkflowAttemptResult(
            attempt_id=required.attempt_id,
            template_key=required.template_key,
            executor_key=attempt.executor_key,
            agent_run_id=agent_run_id,
            agent_runtime_generation=1 if agent_run_id is not None else None,
            steps=steps,
            waits=waits,
        )

    def _accept_draft(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        attempt: ClaimedAttempt,
        required: DispositionRequired,
        workflow_id: UUID,
        thread_id: UUID,
        observation: dict[str, Any],
    ) -> tuple[dict[str, UUID], dict[str, UUID], UUID]:
        if required.replayed:
            draft = load_draft_for_step(connection, required.step_id)
            decision = self._workflow_policy.draft_succeeded(
                workflow_id=workflow_id,
                draft_id=draft.draft_id,
                presentation_fingerprint=draft.presentation_fingerprint,
                recipient_email=draft.policyholder_email,
                subject=draft.subject,
                body=draft.body,
            )
            steps, waits = KernelControl(connection).succeed(
                required,
                output=decision.output,
                outcome_route=decision.outcome_route,
                route_input=decision.route_input,
            )
            return steps, waits, draft.agent_run_id

        agent_run = AgentRuns(connection).complete_for_attempt(required.attempt_id, observation)
        effect = RenewalEmailEffect(
            recipient_email=str(attempt.input["policyholder_email"]),
            subject=str(observation["subject"]),
            body=str(observation["body"]),
        )
        draft_id = uuid4()
        fingerprint = content_fingerprint(effect)
        record_draft(
            connection,
            draft_id=draft_id,
            workflow_id=workflow_id,
            step_id=required.step_id,
            agent_run_id=agent_run.agent_run_id,
            subject=effect.subject,
            body=effect.body,
            policyholder_email=effect.recipient_email,
            presentation_fingerprint=fingerprint,
        )
        event_id = record_event(
            connection,
            event_type="renewal.draft.ready",
            workflow_id=workflow_id,
            actor=Actor("system", "workflow-control-plane"),
            cause=Cause("attempt", str(required.attempt_id)),
            payload={"draft_id": str(draft_id), "step_id": str(required.step_id)},
        )
        DeliveryControl(connection).create(
            domain_event_id=event_id,
            thread_id=thread_id,
            audience=self._delivery_policy.audience,
            message_author=self._delivery_policy.message_author,
            content_descriptor=self._delivery_policy.content_descriptor(
                {
                    "draft_id": str(draft_id),
                    "presentation_fingerprint": fingerprint,
                    "recipient_email": effect.recipient_email,
                    "subject": effect.subject,
                    "body": effect.body,
                }
            ),
            message_content=self._delivery_policy.render_message(
                {"subject": effect.subject, "body": effect.body}
            ),
            retry_policy=self._delivery_policy.retry_policy,
        )
        decision = self._workflow_policy.draft_succeeded(
            workflow_id=workflow_id,
            draft_id=draft_id,
            presentation_fingerprint=fingerprint,
            recipient_email=effect.recipient_email,
            subject=effect.subject,
            body=effect.body,
        )
        steps, waits = KernelControl(connection).succeed(
            required,
            output=decision.output,
            outcome_route=decision.outcome_route,
            route_input=decision.route_input,
        )
        return steps, waits, agent_run.agent_run_id


__all__ = ["RenewalAttemptControl"]
