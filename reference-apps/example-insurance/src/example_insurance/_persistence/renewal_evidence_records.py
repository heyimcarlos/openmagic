"""Private typed transaction-scoped renewal projection persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.evidence import (
    RuntimeDeliveryEvidence,
    RuntimeEvidenceReader,
    RuntimeInstanceEvidence,
)
from psycopg import Connection
from psycopg.rows import dict_row

from example_insurance._persistence.durable_values import (
    boolean_value,
    nonempty_string,
    object_value,
    positive_integer,
    uuid_value,
)
from example_insurance._persistence.renewal_effect_records import (
    EffectEvidenceSource,
    effect_evidence_source,
)
from example_insurance.renewal_approval_policy import (
    ApprovalDecisionKind,
    approval_decision_kind,
)
from example_insurance.renewal_effect_policy import (
    EffectCertainty,
    EffectObservation,
    effect_certainty,
    effect_observation,
)
from example_insurance.renewal_lifecycle_policy import (
    WorkflowLifecycle,
    workflow_lifecycle,
)


@dataclass(frozen=True)
class EvidenceWorkflow:
    command_id: UUID
    workflow_id: UUID
    instance_id: UUID
    thread_id: UUID
    lifecycle: WorkflowLifecycle

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> EvidenceWorkflow:
        return cls(
            command_id=uuid_value(record["start_command_id"]),
            workflow_id=uuid_value(record["workflow_id"]),
            instance_id=uuid_value(record["instance_id"]),
            thread_id=uuid_value(record["thread_id"]),
            lifecycle=workflow_lifecycle(record["lifecycle"]),
        )


@dataclass(frozen=True)
class EvidenceDomainEvent:
    event_id: UUID
    event_type: str
    actor: dict[str, Any]
    cause: dict[str, Any]

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> EvidenceDomainEvent:
        return cls(
            event_id=uuid_value(record["event_id"]),
            event_type=nonempty_string(record["event_type"]),
            actor=object_value(record["actor"]),
            cause=object_value(record["cause"]),
        )


@dataclass(frozen=True)
class EvidenceExternalEffect:
    logical_effect_id: UUID
    certainty: EffectCertainty
    step_id: UUID
    approval_grant_id: UUID
    dispatch_attempt_id: UUID
    effect_fingerprint: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> EvidenceExternalEffect:
        return cls(
            logical_effect_id=uuid_value(record["logical_effect_id"]),
            certainty=effect_certainty(record["certainty"]),
            step_id=uuid_value(record["step_id"]),
            approval_grant_id=uuid_value(record["approval_grant_id"]),
            dispatch_attempt_id=uuid_value(record["dispatch_attempt_id"]),
            effect_fingerprint=nonempty_string(record["effect_fingerprint"]),
        )


@dataclass(frozen=True)
class EvidenceEffectObservation:
    evidence_id: UUID
    classification: EffectObservation
    source: EffectEvidenceSource
    logical_effect_id: UUID
    attempt_id: UUID
    provider_request_id: str | None

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> EvidenceEffectObservation:
        request_id = record["provider_request_id"]
        return cls(
            evidence_id=uuid_value(record["evidence_id"]),
            classification=effect_observation(record["classification"]),
            source=effect_evidence_source(record["source"]),
            logical_effect_id=uuid_value(record["logical_effect_id"]),
            attempt_id=uuid_value(record["attempt_id"]),
            provider_request_id=nonempty_string(request_id) if request_id is not None else None,
        )


@dataclass(frozen=True)
class EvidenceDecision:
    decision_id: UUID
    command_id: UUID
    wait_id: UUID
    draft_id: UUID
    presented_message_id: UUID
    thread_sequence: int
    message_fingerprint: str
    signal_id: UUID
    decision_kind: ApprovalDecisionKind

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> EvidenceDecision:
        return cls(
            decision_id=uuid_value(record["decision_id"]),
            command_id=uuid_value(record["command_id"]),
            wait_id=uuid_value(record["wait_id"]),
            draft_id=uuid_value(record["draft_id"]),
            presented_message_id=uuid_value(record["presented_message_id"]),
            thread_sequence=positive_integer(record["thread_sequence"]),
            message_fingerprint=nonempty_string(record["message_fingerprint"]),
            signal_id=uuid_value(record["signal_id"]),
            decision_kind=approval_decision_kind(record["decision_kind"]),
        )


@dataclass(frozen=True)
class EvidenceApprovalGrant:
    approval_grant_id: UUID
    decision_id: UUID
    step_id: UUID
    effect_fingerprint: str
    consumed: bool
    invalidated: bool

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> EvidenceApprovalGrant:
        return cls(
            approval_grant_id=uuid_value(record["approval_grant_id"]),
            decision_id=uuid_value(record["decision_id"]),
            step_id=uuid_value(record["step_id"]),
            effect_fingerprint=nonempty_string(record["effect_fingerprint"]),
            consumed=boolean_value(record["consumed"]),
            invalidated=boolean_value(record["invalidated"]),
        )


@dataclass(frozen=True)
class RenewalEvidenceSnapshot:
    workflow: EvidenceWorkflow
    runtime: RuntimeInstanceEvidence
    events: tuple[EvidenceDomainEvent, ...]
    deliveries: tuple[RuntimeDeliveryEvidence, ...]
    draft_agent_run_ids: tuple[UUID, ...]
    effects: tuple[EvidenceExternalEffect, ...]
    effect_observations: tuple[EvidenceEffectObservation, ...]
    decisions: tuple[EvidenceDecision, ...]
    grants: tuple[EvidenceApprovalGrant, ...]


def load_renewal_evidence_snapshot(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> RenewalEvidenceSnapshot:
    with connection.cursor(row_factory=dict_row) as cursor:
        workflow_record = cursor.execute(
            "SELECT start_command_id, workflow_id, instance_id, thread_id, lifecycle "
            "FROM example_insurance.renewal_workflows WHERE workflow_id = %s",
            (workflow_id,),
        ).fetchone()
        event_records = cursor.execute(
            "SELECT event_id, event_type, actor, cause "
            "FROM example_insurance.domain_events WHERE workflow_id = %s "
            "ORDER BY occurred_at, event_id",
            (workflow_id,),
        ).fetchall()
        draft_records = cursor.execute(
            "SELECT agent_run_id FROM example_insurance.renewal_drafts "
            "WHERE workflow_id = %s ORDER BY created_at, draft_id",
            (workflow_id,),
        ).fetchall()
        effect_records = cursor.execute(
            "SELECT logical_effect_id, certainty, step_id, approval_grant_id, "
            "dispatch_attempt_id, effect_fingerprint "
            "FROM example_insurance.external_effects "
            "WHERE workflow_id = %s ORDER BY fenced_at, logical_effect_id",
            (workflow_id,),
        ).fetchall()
        observation_records = cursor.execute(
            "SELECT e.evidence_id, e.classification, e.source, e.logical_effect_id, "
            "e.attempt_id, e.provider_request_id "
            "FROM example_insurance.external_effect_evidence e "
            "JOIN example_insurance.external_effects x "
            "ON x.logical_effect_id = e.logical_effect_id WHERE x.workflow_id = %s "
            "ORDER BY e.observed_at, e.evidence_id",
            (workflow_id,),
        ).fetchall()
        decision_records = cursor.execute(
            "SELECT decision_id, command_id, wait_id, draft_id, presented_message_id, "
            "thread_sequence, message_fingerprint, signal_id, decision_kind "
            "FROM example_insurance.renewal_decisions WHERE workflow_id = %s "
            "ORDER BY decided_at, decision_id",
            (workflow_id,),
        ).fetchall()
        grant_records = cursor.execute(
            "SELECT approval_grant_id, decision_id, step_id, effect_fingerprint, "
            "consumed_at IS NOT NULL AS consumed, "
            "invalidated_at IS NOT NULL AS invalidated "
            "FROM example_insurance.approval_grants WHERE workflow_id = %s "
            "ORDER BY created_at, approval_grant_id",
            (workflow_id,),
        ).fetchall()
    if workflow_record is None:
        raise KeyError(f"Renewal Workflow not found: {workflow_id}")
    workflow = EvidenceWorkflow.decode(workflow_record)
    runtime_reader = RuntimeEvidenceReader(connection)
    runtime = runtime_reader.instance(workflow.instance_id)
    events = tuple(EvidenceDomainEvent.decode(record) for record in event_records)
    deliveries = tuple(
        delivery
        for event in events
        if event.event_type == "renewal.draft.ready"
        for delivery in runtime_reader.deliveries(event.event_id)
    )
    effects = tuple(EvidenceExternalEffect.decode(record) for record in effect_records)
    effect_observations = tuple(
        EvidenceEffectObservation.decode(record) for record in observation_records
    )
    decisions = tuple(EvidenceDecision.decode(record) for record in decision_records)
    grants = tuple(EvidenceApprovalGrant.decode(record) for record in grant_records)
    return RenewalEvidenceSnapshot(
        workflow=workflow,
        runtime=runtime,
        events=events,
        deliveries=deliveries,
        draft_agent_run_ids=tuple(uuid_value(record["agent_run_id"]) for record in draft_records),
        effects=effects,
        effect_observations=effect_observations,
        decisions=decisions,
        grants=grants,
    )


__all__ = ["RenewalEvidenceSnapshot", "load_renewal_evidence_snapshot"]
