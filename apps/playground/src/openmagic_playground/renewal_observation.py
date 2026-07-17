"""Versioned typed observer for the public Example Insurance renewal projection."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, NonNegativeInt, ValidationError


class RenewalProjectionDecodeError(ValueError):
    """The serialized renewal projection differs from its versioned contract."""


class _ProjectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RenewalProjectionCorrelations(_ProjectionModel):
    command_id: UUID
    workflow_id: UUID
    instance_id: UUID
    thread_id: UUID
    step_ids: tuple[UUID, ...]
    attempt_ids: tuple[UUID, ...]
    agent_run_ids: tuple[UUID, ...]
    domain_event_ids: tuple[UUID, ...]
    delivery_ids: tuple[UUID, ...]
    message_ids: tuple[UUID, ...]
    draft_agent_run_ids: tuple[UUID, ...]
    decision_ids: tuple[UUID, ...]
    signal_ids: tuple[UUID, ...]
    approval_grant_ids: tuple[UUID, ...]
    logical_effect_ids: tuple[UUID, ...]
    effect_evidence_ids: tuple[UUID, ...]


class RenewalStepOutcome(_ProjectionModel):
    template_key: str
    state: str


class RenewalLineage(_ProjectionModel):
    kind: str
    identifier: str


class RenewalDomainEventOutcome(_ProjectionModel):
    event_id: UUID
    event_type: str
    actor: RenewalLineage
    cause: RenewalLineage


class RenewalEffectEvidenceOutcome(_ProjectionModel):
    evidence_id: UUID
    logical_effect_id: UUID
    attempt_id: UUID
    classification: str
    source: str
    provider_request_id: str | None


class RenewalDecisionOutcome(_ProjectionModel):
    decision_id: UUID
    command_id: UUID
    wait_id: UUID
    draft_id: UUID
    presented_message_id: UUID
    thread_sequence: NonNegativeInt
    message_fingerprint: str
    signal_id: UUID
    decision_kind: str


class RenewalApprovalGrantOutcome(_ProjectionModel):
    approval_grant_id: UUID
    decision_id: UUID
    step_id: UUID
    effect_fingerprint: str
    consumed: bool
    invalidated: bool


class RenewalExternalEffectOutcome(_ProjectionModel):
    logical_effect_id: UUID
    certainty: str
    step_id: UUID
    approval_grant_id: UUID
    dispatch_attempt_id: UUID
    effect_fingerprint: str


class RenewalProjectionOutcomes(_ProjectionModel):
    workflow_lifecycle: str
    instance_state: str
    step_states: dict[UUID, RenewalStepOutcome]
    attempt_states: tuple[str, ...]
    agent_run_states: tuple[str, ...]
    delivery_attempt_states: tuple[tuple[str, ...], ...]
    approval_wait_id: UUID | None
    approval_wait_state: str | None
    approval_wait_ids: tuple[UUID, ...]
    approval_wait_states: tuple[str, ...]
    delivery_states: tuple[str, ...]
    domain_events: tuple[RenewalDomainEventOutcome, ...]
    external_email_effect_count: NonNegativeInt
    external_effect_certainties: tuple[str, ...]
    effect_evidence: tuple[RenewalEffectEvidenceOutcome, ...]
    decisions: tuple[RenewalDecisionOutcome, ...]
    approval_grants: tuple[RenewalApprovalGrantOutcome, ...]
    external_effects: tuple[RenewalExternalEffectOutcome, ...]
    completion_event_count: NonNegativeInt


class RenewalProjection(_ProjectionModel):
    schema_version: Literal["openmagic.evidence.v1"]
    scenario: Literal["renewal_drafting"]
    correlations: RenewalProjectionCorrelations
    outcomes: RenewalProjectionOutcomes
    invariant_violations: tuple[str, ...]
    redacted: Literal[True]


def decode_renewal_projection(payload: str) -> RenewalProjection:
    """Decode and reject any malformed or unversioned renewal projection."""

    try:
        return RenewalProjection.model_validate_json(payload)
    except ValidationError as exc:
        raise RenewalProjectionDecodeError("renewal projection contract mismatch") from exc


__all__ = [
    "RenewalProjection",
    "RenewalProjectionDecodeError",
    "decode_renewal_projection",
]
