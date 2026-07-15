"""Renewal Workflow Attempt recovery and accepted observation transitions."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

from openmagic_runtime.agents import AgentRuns
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.delivery import DeliveryControl
from openmagic_runtime.evidence import content_fingerprint
from openmagic_runtime.kernel.control import KernelControl
from openmagic_runtime.kernel.work import ClaimedAttempt, DispositionRequired
from psycopg import Connection

from example_insurance.renewal_attempt_records import (
    accept_renewal_attempt_result,
    recover_expired_renewal_attempt,
)
from example_insurance.renewal_commands import WorkflowAttemptResult
from example_insurance.renewal_effect_control import RenewalEffectControl
from example_insurance.renewal_effect_types import RenewalEmailEffect
from example_insurance.renewal_policies import RenewalDeliveryPolicy, RenewalWorkflowPolicy
from example_insurance.renewal_records import CommandEventLineage, record_event
from example_insurance.renewal_workflow_records import (
    activation_receipt,
    bind_draft_ready_event,
    load_draft_for_step,
    record_draft,
)

EffectTemplate = Literal["send_renewal_email", "reconcile_renewal_email"]


def _effect_template(value: str) -> EffectTemplate:
    if value == "send_renewal_email":
        return "send_renewal_email"
    if value == "reconcile_renewal_email":
        return "reconcile_renewal_email"
    raise RuntimeError("Command observation does not target an External Effect Step")


class RenewalAttemptControl:
    def __init__(self, *, effect_control: RenewalEffectControl) -> None:
        self._effect_control = effect_control
        self._workflow_policy = RenewalWorkflowPolicy()
        self._delivery_policy = RenewalDeliveryPolicy()

    def recover_expired(self, connection: Connection[tuple[Any, ...]]) -> bool:
        accepted = recover_expired_renewal_attempt(connection)
        if accepted is None:
            return False
        required = accepted.disposition
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
        accepted = accept_renewal_attempt_result(
            connection,
            attempt=attempt,
            worker_id=worker_id,
            observation=observation,
        )
        workflow = accepted.workflow
        required = accepted.disposition
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
        else:
            raise RuntimeError(f"unsupported ordinary renewal Step: {required.template_key}")
        return self._result(attempt, required, steps, waits, agent_run_id)

    def accept_effect_observation(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
        observation: dict[str, Any],
        lineage: CommandEventLineage,
    ) -> WorkflowAttemptResult:
        template = _effect_template(attempt.template_key)
        accepted = accept_renewal_attempt_result(
            connection,
            attempt=attempt,
            worker_id=worker_id,
            observation=observation,
        )
        required = accepted.disposition
        if required.replayed:
            activation = activation_receipt(
                connection,
                instance_id=required.instance_id,
                source_attempt_id=required.attempt_id,
            )
            return self._result(
                attempt,
                required,
                activation.steps,
                activation.waits,
                None,
            )
        if template == "send_renewal_email":
            steps, waits = self._effect_control.accept_email_observation(
                connection,
                required,
                lineage,
            )
        else:
            steps, waits = self._effect_control.accept_reconciliation_observation(
                connection,
                required,
                lineage,
            )
        return self._result(attempt, required, steps, waits, None)

    @staticmethod
    def _result(
        attempt: ClaimedAttempt,
        required: DispositionRequired,
        steps: dict[str, UUID],
        waits: dict[str, UUID],
        agent_run_id: UUID | None,
    ) -> WorkflowAttemptResult:
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
        bind_draft_ready_event(connection, draft_id=draft_id, event_id=event_id)
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
