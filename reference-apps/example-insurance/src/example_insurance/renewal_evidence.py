"""Read-only evidence projection for the renewal drafting scenario."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from openmagic_runtime.evidence import EvidenceRecord, RuntimeEvidenceReader


class RenewalEvidenceProjector:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url

    def to_json(self, workflow_id: UUID) -> str:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            workflow = connection.execute(
                "SELECT start_command_id, instance_id, thread_id, lifecycle FROM "
                "example_insurance.renewal_workflows WHERE workflow_id = %s",
                (workflow_id,),
            ).fetchone()
            if workflow is None:
                raise KeyError(f"Renewal Workflow not found: {workflow_id}")
            runtime = RuntimeEvidenceReader(connection).instance(UUID(str(workflow[1])))
            events = connection.execute(
                "SELECT event_id, event_type, actor, cause "
                "FROM example_insurance.domain_events "
                "WHERE workflow_id = %s "
                "ORDER BY occurred_at, event_id",
                (workflow_id,),
            ).fetchall()
            drafts = connection.execute(
                "SELECT agent_run_id FROM example_insurance.renewal_drafts "
                "WHERE workflow_id = %s ORDER BY created_at, draft_id",
                (workflow_id,),
            ).fetchall()
            evidence_reader = RuntimeEvidenceReader(connection)
            deliveries = tuple(
                delivery
                for event in events
                if str(event[1]) == "renewal.draft.ready"
                for delivery in evidence_reader.deliveries(UUID(str(event[0])))
            )
            effects = connection.execute(
                "SELECT logical_effect_id, certainty, step_id, approval_grant_id, "
                "dispatch_attempt_id, effect_fingerprint FROM "
                "example_insurance.external_effects "
                "WHERE workflow_id = %s ORDER BY fenced_at, logical_effect_id",
                (workflow_id,),
            ).fetchall()
            effect_evidence = connection.execute(
                "SELECT e.evidence_id, e.classification, e.source, e.logical_effect_id, "
                "e.attempt_id, e.provider_request_id "
                "FROM example_insurance.external_effect_evidence e "
                "JOIN example_insurance.external_effects x "
                "ON x.logical_effect_id = e.logical_effect_id WHERE x.workflow_id = %s "
                "ORDER BY e.observed_at, e.evidence_id",
                (workflow_id,),
            ).fetchall()
            decisions = connection.execute(
                "SELECT decision_id, command_id, wait_id, draft_id, presented_message_id, "
                "thread_sequence, message_fingerprint, signal_id, decision_kind "
                "FROM example_insurance.renewal_decisions "
                "WHERE workflow_id = %s ORDER BY decided_at, decision_id",
                (workflow_id,),
            ).fetchall()
            grants = connection.execute(
                "SELECT approval_grant_id, decision_id, step_id, effect_fingerprint, "
                "consumed_at IS NOT NULL, invalidated_at IS NOT NULL "
                "FROM example_insurance.approval_grants "
                "WHERE workflow_id = %s ORDER BY created_at, approval_grant_id",
                (workflow_id,),
            ).fetchall()
            instance_state = connection.execute(
                "SELECT state FROM openmagic_runtime.instances WHERE instance_id = %s",
                (workflow[1],),
            ).fetchone()
        correlations: dict[str, Any] = {
            "command_id": str(workflow[0]),
            "workflow_id": str(workflow_id),
            "instance_id": str(workflow[1]),
            "thread_id": str(workflow[2]),
            "step_ids": [str(step.step_id) for step in runtime.steps],
            "attempt_ids": [str(attempt_id) for attempt_id, _ in runtime.attempts],
            "agent_run_ids": [str(run_id) for run_id, _, _ in runtime.agent_runs],
            "domain_event_ids": [str(event[0]) for event in events],
            "delivery_ids": [str(delivery.delivery_id) for delivery in deliveries],
            "message_ids": [
                str(delivery.delivered_message_id)
                for delivery in deliveries
                if delivery.delivered_message_id is not None
            ],
            "draft_agent_run_ids": [str(draft[0]) for draft in drafts],
            "decision_ids": [str(decision[0]) for decision in decisions],
            "signal_ids": [str(decision[7]) for decision in decisions],
            "approval_grant_ids": [str(grant[0]) for grant in grants],
            "logical_effect_ids": [str(effect[0]) for effect in effects],
            "effect_evidence_ids": [str(item[0]) for item in effect_evidence],
        }
        approval_waits = tuple(
            wait for wait in runtime.waits if wait.template_key == "renewal_draft_approval"
        )
        approval_wait = approval_waits[-1] if approval_waits else None
        return EvidenceRecord(
            schema_version="openmagic.evidence.v1",
            scenario="renewal_drafting",
            correlations=correlations,
            outcomes={
                "workflow_lifecycle": str(workflow[3]),
                "instance_state": str(instance_state[0]) if instance_state is not None else None,
                "step_states": {
                    str(step.step_id): {
                        "template_key": step.template_key,
                        "state": step.state,
                    }
                    for step in runtime.steps
                },
                "attempt_states": [state for _, state in runtime.attempts],
                "agent_run_states": [state for _, _, state in runtime.agent_runs],
                "delivery_attempt_states": [
                    list(delivery.attempt_states) for delivery in deliveries
                ],
                "approval_wait_id": (
                    str(approval_wait.wait_id) if approval_wait is not None else None
                ),
                "approval_wait_state": (approval_wait.state if approval_wait is not None else None),
                "approval_wait_ids": [str(wait.wait_id) for wait in approval_waits],
                "approval_wait_states": [wait.state for wait in approval_waits],
                "delivery_states": [delivery.status for delivery in deliveries],
                "domain_events": [
                    {
                        "event_id": str(event[0]),
                        "event_type": str(event[1]),
                        "actor": dict(event[2]),
                        "cause": dict(event[3]),
                    }
                    for event in events
                ],
                "external_email_effect_count": len(effects),
                "external_effect_certainties": [str(effect[1]) for effect in effects],
                "effect_evidence": [
                    {
                        "evidence_id": str(item[0]),
                        "logical_effect_id": str(item[3]),
                        "attempt_id": str(item[4]),
                        "classification": str(item[1]),
                        "source": str(item[2]),
                        "provider_request_id": str(item[5]) if item[5] is not None else None,
                    }
                    for item in effect_evidence
                ],
                "decisions": [
                    {
                        "decision_id": str(item[0]),
                        "command_id": str(item[1]),
                        "wait_id": str(item[2]),
                        "draft_id": str(item[3]),
                        "presented_message_id": str(item[4]),
                        "thread_sequence": int(item[5]),
                        "message_fingerprint": str(item[6]),
                        "signal_id": str(item[7]),
                        "decision_kind": str(item[8]),
                    }
                    for item in decisions
                ],
                "approval_grants": [
                    {
                        "approval_grant_id": str(item[0]),
                        "decision_id": str(item[1]),
                        "step_id": str(item[2]),
                        "effect_fingerprint": str(item[3]),
                        "consumed": bool(item[4]),
                        "invalidated": bool(item[5]),
                    }
                    for item in grants
                ],
                "external_effects": [
                    {
                        "logical_effect_id": str(item[0]),
                        "certainty": str(item[1]),
                        "step_id": str(item[2]),
                        "approval_grant_id": str(item[3]),
                        "dispatch_attempt_id": str(item[4]),
                        "effect_fingerprint": str(item[5]),
                    }
                    for item in effects
                ],
                "completion_event_count": sum(
                    str(event[1]) == "renewal.outreach.completed" for event in events
                ),
            },
            invariant_violations=(),
            redacted=True,
        ).to_json()


__all__ = ["RenewalEvidenceProjector"]
