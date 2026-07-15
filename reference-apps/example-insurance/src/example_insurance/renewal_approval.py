"""Approval, fenced effect, reconciliation, and completion for renewals."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from openmagic_runtime.commands import Actor, Cause, StateConflict
from openmagic_runtime.evidence import content_fingerprint
from openmagic_runtime.kernel.control import KernelControl
from openmagic_runtime.kernel.transitions import (
    AcceptSignal,
    CloseInstance,
    GuardCurrentAttempt,
    ResolveDeferredStep,
)
from openmagic_runtime.kernel.work import ClaimedAttempt, DispositionRequired
from psycopg import Connection
from psycopg.types.json import Jsonb

from example_insurance.renewal_commands import (
    ApproveRenewalDraft,
    ApproveRenewalDraftResult,
    CancelRenewalOutreach,
    CancelRenewalOutreachResult,
    RequestRenewalRevision,
    RequestRenewalRevisionResult,
    RevokeRenewalAuthority,
    RevokeRenewalAuthorityResult,
)
from example_insurance.renewal_decisions import (
    decision_context,
    decision_facts,
    record_decision,
)
from example_insurance.renewal_effects import (
    ExternalEffectPermit,
    RenewalApprovalPresentation,
    RenewalEmailEffect,
    logical_effect_id,
)
from example_insurance.renewal_policies import (
    ApprovalRejectedDecision,
    CancellationFacts,
    CompletionEffectFact,
    CompletionStepFact,
    EffectAuthorizationFacts,
    RenewalApprovalPolicy,
    RenewalCompletionPolicy,
    RenewalExternalEffectPolicy,
    RenewalLifecyclePolicy,
)
from example_insurance.renewal_records import (
    actor_record,
    cause_record,
    record_effect_evidence,
    record_event,
)


class RenewalApprovalControl:
    def __init__(self) -> None:
        self._approval_policy = RenewalApprovalPolicy()
        self._effect_policy = RenewalExternalEffectPolicy()
        self._lifecycle_policy = RenewalLifecyclePolicy()
        self._completion_policy = RenewalCompletionPolicy()

    @staticmethod
    def presentation(
        connection: Connection[tuple[Any, ...]], workflow_id: UUID
    ) -> RenewalApprovalPresentation:
        row = connection.execute(
            "SELECT w.wait_id, d.draft_id, d.presentation_fingerprint, "
            "d.policyholder_email, d.subject, d.body FROM example_insurance.renewal_workflows r "
            "JOIN example_insurance.renewal_drafts d ON d.workflow_id = r.workflow_id "
            "JOIN openmagic_runtime.waits w ON w.instance_id = r.instance_id "
            "AND (w.input->>'draft_id')::uuid = d.draft_id "
            "WHERE r.workflow_id = %s AND w.state = 'unsatisfied' "
            "ORDER BY w.created_at DESC, w.wait_id DESC LIMIT 1",
            (workflow_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Renewal approval presentation not found: {workflow_id}")
        return RenewalApprovalPresentation(
            workflow_id=workflow_id,
            wait_id=UUID(str(row[0])),
            draft_id=UUID(str(row[1])),
            presentation_fingerprint=str(row[2]),
            proposed_effect=RenewalEmailEffect(
                recipient_email=str(row[3]),
                subject=str(row[4]),
                body=str(row[5]),
            ),
        )

    def approve(
        self,
        command: ApproveRenewalDraft,
        connection: Connection[tuple[Any, ...]],
    ) -> ApproveRenewalDraftResult:
        context = decision_context(connection, command.input)
        decision = self._approval_policy.decide(
            decision_kind="approve",
            facts=decision_facts(context, command.actor, command.input),
        )
        if isinstance(decision, ApprovalRejectedDecision):
            return ApproveRenewalDraftResult(
                outcome=decision.outcome,
                workflow_id=command.input.workflow_id,
                wait_id=command.input.wait_id,
                approval_grant_id=None,
                effect_step_id=None,
            )
        decision_id = uuid4()
        approval_grant_id = uuid4()
        payload = {
            "workflow_id": str(command.input.workflow_id),
            "wait_id": str(command.input.wait_id),
            "draft_id": str(command.input.draft_id),
            "presentation_fingerprint": command.input.presentation_fingerprint,
            "approval_grant_id": str(approval_grant_id),
            "effect_fingerprint": command.input.presentation_fingerprint,
            "recipient_email": command.input.proposed_effect.recipient_email,
            "subject": command.input.proposed_effect.subject,
            "body": command.input.proposed_effect.body,
        }
        signal = KernelControl(connection).accept_signal(
            AcceptSignal(
                signal_id=command.command_id,
                instance_id=context.instance_id,
                wait_id=command.input.wait_id,
                signal_type="renewal.draft.decision",
                schema_version=1,
                payload=payload,
                route_key=decision.route_key,
            )
        )
        effect_step_id = signal.steps["email_effect"]
        record_decision(
            connection,
            decision_id=decision_id,
            command_id=command.command_id,
            actor=command.actor,
            cause=command.cause,
            decision_kind="approve",
            value=command.input,
            revision_instruction=None,
        )
        connection.execute(
            "INSERT INTO example_insurance.approval_grants "
            "(approval_grant_id, decision_id, workflow_id, step_id, effect_fingerprint, "
            "actor, cause) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                approval_grant_id,
                decision_id,
                command.input.workflow_id,
                effect_step_id,
                command.input.presentation_fingerprint,
                Jsonb(actor_record(command.actor)),
                Jsonb(cause_record(command.cause)),
            ),
        )
        record_event(
            connection,
            event_type="renewal.draft.approved",
            workflow_id=command.input.workflow_id,
            actor=command.actor,
            cause=command.cause,
            payload={
                "decision_id": str(decision_id),
                "approval_grant_id": str(approval_grant_id),
                "draft_id": str(command.input.draft_id),
                "step_id": str(effect_step_id),
            },
        )
        return ApproveRenewalDraftResult(
            outcome="approved",
            workflow_id=command.input.workflow_id,
            wait_id=command.input.wait_id,
            approval_grant_id=approval_grant_id,
            effect_step_id=effect_step_id,
        )

    def request_revision(
        self,
        command: RequestRenewalRevision,
        connection: Connection[tuple[Any, ...]],
    ) -> RequestRenewalRevisionResult:
        context = decision_context(connection, command.input)
        decision = self._approval_policy.decide(
            decision_kind="request_revision",
            facts=decision_facts(context, command.actor, command.input),
        )
        if isinstance(decision, ApprovalRejectedDecision):
            return RequestRenewalRevisionResult(
                outcome=decision.outcome,
                workflow_id=command.input.workflow_id,
                wait_id=command.input.wait_id,
                revision_step_id=None,
            )
        payload = {
            "workflow_id": str(command.input.workflow_id),
            "wait_id": str(command.input.wait_id),
            "draft_id": str(command.input.draft_id),
            "presentation_fingerprint": command.input.presentation_fingerprint,
            "recipient_email": command.input.proposed_effect.recipient_email,
            "subject": command.input.proposed_effect.subject,
            "body": command.input.proposed_effect.body,
            "thread_id": str(context.thread_id),
            "revision_instruction": command.input.revision_instruction,
            "policy_number": context.policy_number,
            "policyholder_name": context.policyholder_name,
            "policyholder_email": context.policyholder_email,
            "renewal_date": context.renewal_date,
            "expiring_premium_cents": context.expiring_premium_cents,
        }
        signal = KernelControl(connection).accept_signal(
            AcceptSignal(
                signal_id=command.command_id,
                instance_id=context.instance_id,
                wait_id=command.input.wait_id,
                signal_type="renewal.draft.decision",
                schema_version=1,
                payload=payload,
                route_key=decision.route_key,
            )
        )
        decision_id = uuid4()
        record_decision(
            connection,
            decision_id=decision_id,
            command_id=command.command_id,
            actor=command.actor,
            cause=command.cause,
            decision_kind="request_revision",
            value=command.input,
            revision_instruction=command.input.revision_instruction,
        )
        revision_step_id = signal.steps["revision_draft"]
        record_event(
            connection,
            event_type="renewal.draft.revision_requested",
            workflow_id=command.input.workflow_id,
            actor=command.actor,
            cause=command.cause,
            payload={
                "decision_id": str(decision_id),
                "draft_id": str(command.input.draft_id),
                "revision_step_id": str(revision_step_id),
            },
        )
        return RequestRenewalRevisionResult(
            outcome="revision_requested",
            workflow_id=command.input.workflow_id,
            wait_id=command.input.wait_id,
            revision_step_id=revision_step_id,
        )

    def revoke(
        self,
        command: RevokeRenewalAuthority,
        connection: Connection[tuple[Any, ...]],
    ) -> RevokeRenewalAuthorityResult:
        row = connection.execute(
            "SELECT authority_revoked_at, authorized_actor_id "
            "FROM example_insurance.renewal_workflows WHERE workflow_id = %s FOR UPDATE",
            (command.input.workflow_id,),
        ).fetchone()
        if row is None:
            raise StateConflict("Renewal Workflow does not exist")
        if not self._lifecycle_policy.authorizes_revocation(
            actor_kind=command.actor.kind,
            actor_id=command.actor.identifier,
        ):
            raise StateConflict("Actor is not authorized to revoke approval authority")
        if str(row[1]) != command.input.actor_id:
            raise StateConflict("Authority revocation targets another Actor")
        if row[0] is not None:
            return RevokeRenewalAuthorityResult("already_revoked", command.input.workflow_id)
        connection.execute(
            "UPDATE example_insurance.renewal_workflows SET authority_revoked_at = "
            "clock_timestamp() WHERE workflow_id = %s",
            (command.input.workflow_id,),
        )
        connection.execute(
            "UPDATE example_insurance.approval_grants SET invalidated_at = clock_timestamp() "
            "WHERE workflow_id = %s AND consumed_at IS NULL AND invalidated_at IS NULL",
            (command.input.workflow_id,),
        )
        record_event(
            connection,
            event_type="renewal.approval_authority.revoked",
            workflow_id=command.input.workflow_id,
            actor=command.actor,
            cause=command.cause,
            payload={"authorized_actor_id": command.input.actor_id},
        )
        return RevokeRenewalAuthorityResult("revoked", command.input.workflow_id)

    def cancel(
        self,
        command: CancelRenewalOutreach,
        connection: Connection[tuple[Any, ...]],
    ) -> CancelRenewalOutreachResult:
        row = connection.execute(
            "SELECT instance_id, lifecycle, authorized_actor_kind, authorized_actor_id "
            "FROM example_insurance.renewal_workflows "
            "WHERE workflow_id = %s FOR UPDATE",
            (command.input.workflow_id,),
        ).fetchone()
        if row is None:
            raise StateConflict("Renewal Workflow does not exist")
        instance_id = UUID(str(row[0]))
        lifecycle = str(row[1])
        actor_authorized = self._lifecycle_policy.actor_can_cancel(
            actor_kind=command.actor.kind,
            actor_id=command.actor.identifier,
            authorized_actor_kind=str(row[2]),
            authorized_actor_id=str(row[3]),
        )
        crossed = connection.execute(
            "SELECT 1 FROM example_insurance.external_effects WHERE workflow_id = %s LIMIT 1",
            (command.input.workflow_id,),
        ).fetchone()
        outcome = self._lifecycle_policy.cancellation_outcome(
            CancellationFacts(
                lifecycle=lifecycle,
                actor_authorized=actor_authorized,
                dispatch_boundary_crossed=crossed is not None,
            )
        )
        if outcome == "unauthorized":
            raise StateConflict("Actor is not authorized to cancel renewal outreach")
        if outcome == "already_completed":
            return CancelRenewalOutreachResult(
                "already_completed", command.input.workflow_id, instance_id
            )
        if outcome == "already_cancelled":
            return CancelRenewalOutreachResult(
                "already_cancelled", command.input.workflow_id, instance_id
            )
        if outcome == "too_late":
            return CancelRenewalOutreachResult("too_late", command.input.workflow_id, instance_id)
        connection.execute(
            "UPDATE example_insurance.approval_grants SET invalidated_at = clock_timestamp() "
            "WHERE workflow_id = %s AND consumed_at IS NULL AND invalidated_at IS NULL",
            (command.input.workflow_id,),
        )
        connection.execute(
            "UPDATE example_insurance.renewal_workflows SET lifecycle = 'cancelled' "
            "WHERE workflow_id = %s",
            (command.input.workflow_id,),
        )
        record_event(
            connection,
            event_type="renewal.outreach.cancelled",
            workflow_id=command.input.workflow_id,
            actor=command.actor,
            cause=command.cause,
            payload={"instance_id": str(instance_id)},
        )
        KernelControl(connection).close(CloseInstance(command.command_id, instance_id))
        return CancelRenewalOutreachResult("cancelled", command.input.workflow_id, instance_id)

    def authorize_dispatch(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        attempt: ClaimedAttempt,
        worker_id: str,
    ) -> ExternalEffectPermit:
        workflow = connection.execute(
            "SELECT workflow_id, lifecycle, authority_revoked_at IS NOT NULL "
            "FROM example_insurance.renewal_workflows WHERE instance_id = %s FOR UPDATE",
            (attempt.instance_id,),
        ).fetchone()
        if workflow is None:
            raise StateConflict("Renewal Workflow is unavailable")
        grant = connection.execute(
            "SELECT approval_grant_id, effect_fingerprint, invalidated_at, consumed_at "
            "FROM example_insurance.approval_grants WHERE workflow_id = %s AND step_id = %s "
            "FOR UPDATE",
            (workflow[0], attempt.step_id),
        ).fetchone()
        if grant is None:
            raise StateConflict("Exact Approval Grant is unavailable")
        effect = connection.execute(
            "SELECT logical_effect_id, certainty, effect_fingerprint, provider_idempotency_key "
            "FROM example_insurance.external_effects WHERE step_id = %s FOR UPDATE",
            (attempt.step_id,),
        ).fetchone()
        existing_certainty = str(effect[1]) if effect is not None else None
        guard = KernelControl(connection).guard_current_attempt(
            GuardCurrentAttempt(
                instance_id=attempt.instance_id,
                step_id=attempt.step_id,
                attempt_id=attempt.attempt_id,
                attempt_number=attempt.attempt_number,
            )
        )
        durable = connection.execute(
            "SELECT a.worker_id, s.template_key, s.input FROM openmagic_runtime.attempts a "
            "JOIN openmagic_runtime.steps s ON s.step_id = a.step_id "
            "WHERE a.attempt_id = %s",
            (attempt.attempt_id,),
        ).fetchone()
        durable_claim_matches = not (
            durable is None
            or str(durable[0]) != worker_id
            or str(durable[1]) != attempt.template_key
            or dict(durable[2]) != attempt.input
        )
        durable_effect = RenewalEmailEffect(
            recipient_email=str(attempt.input["recipient_email"]),
            subject=str(attempt.input["subject"]),
            body=str(attempt.input["body"]),
        )
        effect_id = logical_effect_id(attempt.step_id)
        durable_effect_matches = content_fingerprint(durable_effect) == str(grant[1]) and (
            effect is None
            or (UUID(str(effect[0])) == effect_id and str(effect[2]) == str(grant[1]))
        )
        self._effect_policy.authorize_dispatch(
            facts=EffectAuthorizationFacts(
                lifecycle_active=str(workflow[1]) == "active",
                authority_revoked=bool(workflow[2]),
                grant_matches_step=UUID(str(grant[0]))
                == UUID(str(attempt.input["approval_grant_id"])),
                fingerprint_matches_grant=str(grant[1]) == str(attempt.input["effect_fingerprint"]),
                grant_valid=grant[2] is None,
                grant_consumption_consistent=not (effect is None and grant[3] is not None),
                durable_claim_matches=durable_claim_matches,
                durable_effect_matches=durable_effect_matches,
                existing_certainty=existing_certainty,
            )
        )
        guard.require_usable()
        provider_key = str(effect_id)
        if effect is None:
            connection.execute(
                "INSERT INTO example_insurance.external_effects "
                "(logical_effect_id, workflow_id, step_id, approval_grant_id, "
                "effect_fingerprint, provider_idempotency_key, dispatch_attempt_id, "
                "dispatch_attempt_number, certainty) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'dispatching')",
                (
                    effect_id,
                    workflow[0],
                    attempt.step_id,
                    grant[0],
                    grant[1],
                    provider_key,
                    attempt.attempt_id,
                    attempt.attempt_number,
                ),
            )
            connection.execute(
                "UPDATE example_insurance.approval_grants SET consumed_at = clock_timestamp() "
                "WHERE approval_grant_id = %s",
                (grant[0],),
            )
        else:
            provider_key = str(effect[3])
            connection.execute(
                "UPDATE example_insurance.external_effects SET dispatch_attempt_id = %s, "
                "dispatch_attempt_number = %s, certainty = 'dispatching', "
                "updated_at = clock_timestamp() WHERE logical_effect_id = %s",
                (attempt.attempt_id, attempt.attempt_number, effect_id),
            )
        record_event(
            connection,
            event_type="external_effect.dispatch_started",
            workflow_id=UUID(str(workflow[0])),
            actor=Actor("system", "workflow-worker"),
            cause=Cause("attempt", str(attempt.attempt_id)),
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
        )

    def recover_fenced_attempt(
        self,
        connection: Connection[tuple[Any, ...]],
        required: DispositionRequired,
    ) -> tuple[dict[str, UUID], dict[str, UUID]] | None:
        if required.template_key != "send_renewal_email":
            return None
        effect = connection.execute(
            "SELECT logical_effect_id, workflow_id, provider_idempotency_key, certainty "
            "FROM example_insurance.external_effects WHERE step_id = %s FOR UPDATE",
            (required.step_id,),
        ).fetchone()
        control = KernelControl(connection)
        if effect is None:
            if required.attempt_number < self._effect_policy.maximum_attempts:
                control.retry(required)
            else:
                control.fail(required, failure={"class": "attempt_budget_exhausted"})
            return {}, {}
        effect_id = UUID(str(effect[0]))
        connection.execute(
            "UPDATE example_insurance.external_effects SET certainty = 'uncertain', "
            "updated_at = clock_timestamp() WHERE logical_effect_id = %s",
            (effect_id,),
        )
        record_effect_evidence(
            connection,
            logical_effect_id=effect_id,
            attempt_id=required.attempt_id,
            classification="uncertain",
            source="worker_loss_after_fence",
            provider_request_id=None,
        )
        return control.defer(
            required,
            outcome_route="reconcile_email",
            route_input={
                "workflow_id": str(effect[1]),
                "effect_step_id": str(required.step_id),
                "basis_attempt_id": str(required.attempt_id),
                "logical_effect_id": str(effect_id),
                "provider_idempotency_key": str(effect[2]),
            },
        )

    def accept_email_observation(
        self,
        connection: Connection[tuple[Any, ...]],
        required: DispositionRequired,
    ) -> tuple[dict[str, UUID], dict[str, UUID]]:
        effect = connection.execute(
            "SELECT logical_effect_id, workflow_id, provider_idempotency_key, "
            "dispatch_attempt_id FROM example_insurance.external_effects "
            "WHERE step_id = %s FOR UPDATE",
            (required.step_id,),
        ).fetchone()
        if effect is None or UUID(str(effect[3])) != required.attempt_id:
            raise StateConflict("Attempt observation has no matching dispatch fence")
        classification = str(required.observation["classification"])
        effect_id = UUID(str(effect[0]))
        self._record_effect_observation(
            connection,
            effect_id=effect_id,
            required=required,
            classification=classification,
            source="provider_response",
        )
        disposition = self._effect_policy.result_disposition(
            classification=classification,
            attempt_number=required.attempt_number,
            maximum_attempts=self._effect_policy.maximum_attempts,
        )
        control = KernelControl(connection)
        if disposition == "succeed":
            result = control.succeed(
                required,
                output={
                    "logical_effect_id": str(effect_id),
                    "classification": "applied",
                },
            )
            self._complete_if_ready(connection, UUID(str(effect[1])), required.attempt_id)
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
                "workflow_id": str(effect[1]),
                "effect_step_id": str(required.step_id),
                "basis_attempt_id": str(required.attempt_id),
                "logical_effect_id": str(effect_id),
                "provider_idempotency_key": str(effect[2]),
            },
        )

    def accept_reconciliation_observation(
        self,
        connection: Connection[tuple[Any, ...]],
        required: DispositionRequired,
    ) -> tuple[dict[str, UUID], dict[str, UUID]]:
        step_input = connection.execute(
            "SELECT s.input, a.attempt_number FROM openmagic_runtime.steps s "
            "JOIN openmagic_runtime.attempts a ON a.attempt_id = "
            "(s.input->>'basis_attempt_id')::uuid WHERE s.step_id = %s",
            (required.step_id,),
        ).fetchone()
        if step_input is None:
            raise StateConflict("Reconciliation Step input is unavailable")
        value = dict(step_input[0])
        effect_id = UUID(str(value["logical_effect_id"]))
        effect = connection.execute(
            "SELECT workflow_id FROM example_insurance.external_effects "
            "WHERE logical_effect_id = %s FOR UPDATE",
            (effect_id,),
        ).fetchone()
        if effect is None:
            raise StateConflict("Reconciliation target External Effect is unavailable")
        classification = str(required.observation["classification"])
        self._record_effect_observation(
            connection,
            effect_id=effect_id,
            required=required,
            classification=classification,
            source="provider_lookup",
        )
        disposition = self._effect_policy.reconciliation_disposition(
            classification=classification,
            effect_attempt_number=int(step_input[1]),
            reconciliation_attempt_number=required.attempt_number,
            maximum_effect_attempts=self._effect_policy.maximum_attempts,
            maximum_reconciliation_attempts=self._effect_policy.maximum_attempts,
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
                "logical_effect_id": str(effect_id),
                "classification": classification,
            },
        )
        original_step_id = UUID(str(value["effect_step_id"]))
        basis_attempt_id = UUID(str(value["basis_attempt_id"]))
        if disposition == "confirm":
            control.resolve_deferred(
                ResolveDeferredStep(
                    source_id=required.attempt_id,
                    instance_id=required.instance_id,
                    step_id=original_step_id,
                    basis_attempt_id=basis_attempt_id,
                    action="succeed",
                    output={
                        "logical_effect_id": str(effect_id),
                        "classification": "applied",
                    },
                )
            )
            self._complete_if_ready(connection, UUID(str(effect[0])), required.attempt_id)
        elif disposition == "retry_effect":
            control.resolve_deferred(
                ResolveDeferredStep(
                    source_id=required.attempt_id,
                    instance_id=required.instance_id,
                    step_id=original_step_id,
                    basis_attempt_id=basis_attempt_id,
                    action="retry",
                )
            )
        else:
            control.resolve_deferred(
                ResolveDeferredStep(
                    source_id=required.attempt_id,
                    instance_id=required.instance_id,
                    step_id=original_step_id,
                    basis_attempt_id=basis_attempt_id,
                    action="fail",
                    failure={"class": "external_effect_attempt_budget_exhausted"},
                )
            )
        return {}, {}

    def _record_effect_observation(
        self,
        connection: Connection[tuple[Any, ...]],
        *,
        effect_id: UUID,
        required: DispositionRequired,
        classification: str,
        source: str,
    ) -> None:
        if classification not in {"applied", "not_applied", "uncertain"}:
            raise StateConflict("Provider observation classification is unsupported")
        connection.execute(
            "UPDATE example_insurance.external_effects SET certainty = %s, "
            "updated_at = clock_timestamp() WHERE logical_effect_id = %s",
            (classification, effect_id),
        )
        record_effect_evidence(
            connection,
            logical_effect_id=effect_id,
            attempt_id=required.attempt_id,
            classification=classification,
            source=source,
            provider_request_id=str(required.observation["provider_request_id"]),
        )
        workflow = connection.execute(
            "SELECT workflow_id FROM example_insurance.external_effects "
            "WHERE logical_effect_id = %s",
            (effect_id,),
        ).fetchone()
        if workflow is None:
            raise StateConflict("External Effect disappeared while recording evidence")
        record_event(
            connection,
            event_type=f"external_effect.{classification}",
            workflow_id=UUID(str(workflow[0])),
            actor=Actor("system", "workflow-control-plane"),
            cause=Cause("attempt", str(required.attempt_id)),
            payload={
                "logical_effect_id": str(effect_id),
                "attempt_id": str(required.attempt_id),
                "classification": classification,
            },
        )

    def _complete_if_ready(
        self,
        connection: Connection[tuple[Any, ...]],
        workflow_id: UUID,
        source_attempt_id: UUID,
    ) -> None:
        workflow = connection.execute(
            "SELECT instance_id, lifecycle FROM example_insurance.renewal_workflows "
            "WHERE workflow_id = %s FOR UPDATE",
            (workflow_id,),
        ).fetchone()
        if workflow is None or str(workflow[1]) != "active":
            return
        steps = connection.execute(
            "SELECT state, output_digest IS NOT NULL FROM openmagic_runtime.steps "
            "WHERE instance_id = %s",
            (workflow[0],),
        ).fetchall()
        effects = connection.execute(
            "SELECT e.certainty, EXISTS (SELECT 1 FROM "
            "example_insurance.external_effect_evidence v WHERE "
            "v.logical_effect_id = e.logical_effect_id AND v.classification = 'applied' "
            "AND v.source IN ('provider_response', 'provider_lookup')) "
            "FROM example_insurance.external_effects e WHERE e.workflow_id = %s",
            (workflow_id,),
        ).fetchall()
        if not self._completion_policy.is_complete(
            steps=tuple(CompletionStepFact(str(row[0]), bool(row[1])) for row in steps),
            effects=tuple(CompletionEffectFact(str(row[0]), bool(row[1])) for row in effects),
        ):
            return
        connection.execute(
            "UPDATE example_insurance.renewal_workflows SET lifecycle = 'completed' "
            "WHERE workflow_id = %s",
            (workflow_id,),
        )
        record_event(
            connection,
            event_type="renewal.outreach.completed",
            workflow_id=workflow_id,
            actor=Actor("system", "workflow-control-plane"),
            cause=Cause("attempt", str(source_attempt_id)),
            payload={"instance_id": str(workflow[0])},
        )
        KernelControl(connection).close(
            CloseInstance(command_id=source_attempt_id, instance_id=UUID(str(workflow[0])))
        )


__all__ = ["RenewalApprovalControl"]
