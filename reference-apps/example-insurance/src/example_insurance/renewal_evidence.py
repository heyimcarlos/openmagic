"""Read-only evidence projection for the renewal drafting scenario."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from openmagic_runtime.evidence import EvidenceRecord

from example_insurance._persistence.renewal_evidence_records import (
    RenewalEvidenceSnapshot,
    load_renewal_evidence_snapshot,
)
from example_insurance._persistence.transaction_modes import set_repeatable_read_only


def _correlations(snapshot: RenewalEvidenceSnapshot) -> dict[str, Any]:
    workflow = snapshot.workflow
    runtime = snapshot.runtime
    return {
        "command_id": str(workflow.command_id),
        "workflow_id": str(workflow.workflow_id),
        "instance_id": str(workflow.instance_id),
        "thread_id": str(workflow.thread_id),
        "step_ids": [str(step.step_id) for step in runtime.steps],
        "attempt_ids": [str(attempt.attempt_id) for attempt in runtime.attempts],
        "agent_run_ids": [str(run.agent_run_id) for run in runtime.agent_runs],
        "domain_event_ids": [str(event.event_id) for event in snapshot.events],
        "delivery_ids": [str(delivery.delivery_id) for delivery in snapshot.deliveries],
        "message_ids": [
            str(delivery.delivered_message_id)
            for delivery in snapshot.deliveries
            if delivery.delivered_message_id is not None
        ],
        "draft_agent_run_ids": [str(run_id) for run_id in snapshot.draft_agent_run_ids],
        "decision_ids": [str(decision.decision_id) for decision in snapshot.decisions],
        "signal_ids": [str(decision.signal_id) for decision in snapshot.decisions],
        "approval_grant_ids": [str(grant.approval_grant_id) for grant in snapshot.grants],
        "logical_effect_ids": [str(effect.logical_effect_id) for effect in snapshot.effects],
        "effect_evidence_ids": [str(item.evidence_id) for item in snapshot.effect_observations],
    }


def _outcomes(snapshot: RenewalEvidenceSnapshot) -> dict[str, Any]:
    runtime = snapshot.runtime
    approval_waits = tuple(
        wait for wait in runtime.waits if wait.template_key == "renewal_draft_approval"
    )
    approval_wait = approval_waits[-1] if approval_waits else None
    return {
        "workflow_lifecycle": snapshot.workflow.lifecycle,
        "instance_state": runtime.state,
        "step_states": {
            str(step.step_id): {"template_key": step.template_key, "state": step.state}
            for step in runtime.steps
        },
        "attempt_states": [attempt.state for attempt in runtime.attempts],
        "agent_run_states": [run.state for run in runtime.agent_runs],
        "delivery_attempt_states": [
            [attempt.state for attempt in delivery.attempts] for delivery in snapshot.deliveries
        ],
        "approval_wait_id": str(approval_wait.wait_id) if approval_wait is not None else None,
        "approval_wait_state": approval_wait.state if approval_wait is not None else None,
        "approval_wait_ids": [str(wait.wait_id) for wait in approval_waits],
        "approval_wait_states": [wait.state for wait in approval_waits],
        "delivery_states": [delivery.status for delivery in snapshot.deliveries],
        "domain_events": [
            {
                "event_id": str(event.event_id),
                "event_type": event.event_type,
                "actor": event.actor,
                "cause": event.cause,
            }
            for event in snapshot.events
        ],
        "external_email_effect_count": len(snapshot.effects),
        "external_effect_certainties": [effect.certainty for effect in snapshot.effects],
        "effect_evidence": [
            {
                "evidence_id": str(item.evidence_id),
                "logical_effect_id": str(item.logical_effect_id),
                "attempt_id": str(item.attempt_id),
                "classification": item.classification,
                "source": item.source,
                "provider_request_id": item.provider_request_id,
            }
            for item in snapshot.effect_observations
        ],
        "decisions": [
            {
                "decision_id": str(item.decision_id),
                "command_id": str(item.command_id),
                "wait_id": str(item.wait_id),
                "draft_id": str(item.draft_id),
                "presented_message_id": str(item.presented_message_id),
                "thread_sequence": item.thread_sequence,
                "message_fingerprint": item.message_fingerprint,
                "signal_id": str(item.signal_id),
                "decision_kind": item.decision_kind,
            }
            for item in snapshot.decisions
        ],
        "approval_grants": [
            {
                "approval_grant_id": str(item.approval_grant_id),
                "decision_id": str(item.decision_id),
                "step_id": str(item.step_id),
                "effect_fingerprint": item.effect_fingerprint,
                "consumed": item.consumed,
                "invalidated": item.invalidated,
            }
            for item in snapshot.grants
        ],
        "external_effects": [
            {
                "logical_effect_id": str(item.logical_effect_id),
                "certainty": item.certainty,
                "step_id": str(item.step_id),
                "approval_grant_id": str(item.approval_grant_id),
                "dispatch_attempt_id": str(item.dispatch_attempt_id),
                "effect_fingerprint": item.effect_fingerprint,
            }
            for item in snapshot.effects
        ],
        "completion_event_count": sum(
            event.event_type == "renewal.outreach.completed" for event in snapshot.events
        ),
    }


class RenewalEvidenceProjector:
    def __init__(self, *, database_url: str) -> None:
        self._database_url = database_url

    def to_json(self, workflow_id: UUID) -> str:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            set_repeatable_read_only(connection)
            return read_renewal_evidence(connection, workflow_id).to_json()


def read_renewal_evidence(
    connection: psycopg.Connection[tuple[Any, ...]], workflow_id: UUID
) -> EvidenceRecord:
    """Project one renewal through a caller-owned evidence snapshot."""

    snapshot = load_renewal_evidence_snapshot(connection, workflow_id)
    return EvidenceRecord(
        schema_version="openmagic.evidence.v1",
        scenario="renewal_drafting",
        correlations=_correlations(snapshot),
        outcomes=_outcomes(snapshot),
        invariant_violations=(),
        redacted=True,
    )


__all__ = ["RenewalEvidenceProjector", "read_renewal_evidence"]
