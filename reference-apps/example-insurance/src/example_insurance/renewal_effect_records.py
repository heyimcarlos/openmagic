"""Transaction-bound persistence for renewal External Effects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from openmagic_runtime.commands import StateConflict
from openmagic_runtime.kernel.attempt_guard import CurrentAttemptGuard
from openmagic_runtime.kernel.control import KernelControl
from openmagic_runtime.kernel.records import read_attempt, read_step
from openmagic_runtime.kernel.transitions import GuardCurrentAttempt
from openmagic_runtime.kernel.work import ClaimedAttempt
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from example_insurance.renewal_effect_policy import (
    DispatchClaim,
    DurableApprovalGrant,
    DurableExternalEffect,
    DurableWorkflowAuthority,
    EffectObservation,
    effect_certainty,
)
from example_insurance.renewal_effect_types import RenewalEmailEffect
from example_insurance.renewal_grant_records import mark_grant_consumed
from example_insurance.renewal_lifecycle_policy import workflow_lifecycle

EffectEvidenceSource = Literal[
    "provider_response",
    "provider_lookup",
    "worker_loss_after_fence",
]


def effect_evidence_source(value: object) -> EffectEvidenceSource:
    if value == "provider_response":
        return "provider_response"
    if value == "provider_lookup":
        return "provider_lookup"
    if value == "worker_loss_after_fence":
        return "worker_loss_after_fence"
    raise RuntimeError("External Effect evidence has an invalid source")


@dataclass(frozen=True)
class ReconciliationTarget:
    effect: DurableExternalEffect
    effect_step_id: UUID
    basis_attempt_id: UUID
    effect_attempt_number: int


@dataclass(frozen=True)
class LockedDispatchAuthority:
    guard: CurrentAttemptGuard
    workflow: DurableWorkflowAuthority
    grant: DurableApprovalGrant
    durable_claim: DispatchClaim
    effect: DurableExternalEffect | None


def _workflow_authority(record: Mapping[str, Any]) -> DurableWorkflowAuthority:
    return DurableWorkflowAuthority(
        workflow_id=UUID(str(record["workflow_id"])),
        instance_id=UUID(str(record["instance_id"])),
        lifecycle=workflow_lifecycle(record["lifecycle"]),
        authority_revoked=bool(record["authority_revoked"]),
    )


def _approval_grant(record: Mapping[str, Any]) -> DurableApprovalGrant:
    return DurableApprovalGrant(
        approval_grant_id=UUID(str(record["approval_grant_id"])),
        workflow_id=UUID(str(record["workflow_id"])),
        step_id=UUID(str(record["step_id"])),
        effect_fingerprint=str(record["effect_fingerprint"]),
        invalidated=bool(record["invalidated"]),
        consumed=bool(record["consumed"]),
    )


def _external_effect(record: Mapping[str, Any]) -> DurableExternalEffect:
    return DurableExternalEffect(
        logical_effect_id=UUID(str(record["logical_effect_id"])),
        workflow_id=UUID(str(record["workflow_id"])),
        step_id=UUID(str(record["step_id"])),
        approval_grant_id=UUID(str(record["approval_grant_id"])),
        effect_fingerprint=str(record["effect_fingerprint"]),
        provider_idempotency_key=str(record["provider_idempotency_key"]),
        dispatch_attempt_id=UUID(str(record["dispatch_attempt_id"])),
        dispatch_attempt_number=int(record["dispatch_attempt_number"]),
        certainty=effect_certainty(record["certainty"]),
    )


def _record_effect_evidence(
    connection: Connection[tuple[Any, ...]],
    *,
    logical_effect_id: UUID,
    attempt_id: UUID,
    classification: EffectObservation,
    source: EffectEvidenceSource,
    provider_request_id: str | None,
) -> None:
    connection.execute(
        "INSERT INTO example_insurance.external_effect_evidence "
        "(evidence_id, logical_effect_id, attempt_id, classification, source, "
        "provider_request_id, details) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            uuid4(),
            logical_effect_id,
            attempt_id,
            classification,
            source,
            provider_request_id,
            Jsonb({"classification": classification, "source": source}),
        ),
    )


def _claim_from_input(
    *,
    instance_id: UUID,
    step_id: UUID,
    attempt_id: UUID,
    attempt_number: int,
    worker_id: str,
    template_key: str,
    step_input: dict[str, Any],
) -> DispatchClaim:
    try:
        return DispatchClaim(
            instance_id=instance_id,
            step_id=step_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            worker_id=worker_id,
            template_key=template_key,
            approval_grant_id=UUID(str(step_input["approval_grant_id"])),
            effect_fingerprint=str(step_input["effect_fingerprint"]),
            effect=RenewalEmailEffect(
                recipient_email=str(step_input["recipient_email"]),
                subject=str(step_input["subject"]),
                body=str(step_input["body"]),
            ),
        )
    except (KeyError, ValueError) as error:
        raise StateConflict(
            "Dispatch Attempt input is not a typed approved email effect"
        ) from error


def requested_dispatch_claim(attempt: ClaimedAttempt, worker_id: str) -> DispatchClaim:
    return _claim_from_input(
        instance_id=attempt.instance_id,
        step_id=attempt.step_id,
        attempt_id=attempt.attempt_id,
        attempt_number=attempt.attempt_number,
        worker_id=worker_id,
        template_key=attempt.template_key,
        step_input=attempt.input,
    )


def _lock_workflow_authority(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> DurableWorkflowAuthority:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT workflow_id, instance_id, lifecycle, "
            "authority_revoked_at IS NOT NULL AS authority_revoked "
            "FROM example_insurance.renewal_workflows "
            "WHERE instance_id = %s FOR UPDATE",
            (instance_id,),
        ).fetchone()
    if record is None:
        raise StateConflict("Renewal Workflow is unavailable")
    return _workflow_authority(record)


def _load_durable_dispatch_claim(
    connection: Connection[tuple[Any, ...]], attempt_id: UUID
) -> DispatchClaim:
    attempt = read_attempt(connection, attempt_id)
    if attempt is None:
        raise StateConflict("Durable dispatch Attempt is unavailable")
    return _claim_from_input(
        instance_id=attempt.instance_id,
        step_id=attempt.step_id,
        attempt_id=attempt_id,
        attempt_number=attempt.attempt_number,
        worker_id=attempt.worker_id,
        template_key=attempt.template_key,
        step_input=attempt.step_input,
    )


def _lock_approval_grant(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID, step_id: UUID
) -> DurableApprovalGrant:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT approval_grant_id, workflow_id, step_id, effect_fingerprint, "
            "invalidated_at IS NOT NULL AS invalidated, "
            "consumed_at IS NOT NULL AS consumed "
            "FROM example_insurance.approval_grants "
            "WHERE workflow_id = %s AND step_id = %s FOR UPDATE",
            (workflow_id, step_id),
        ).fetchone()
    if record is None:
        raise StateConflict("Exact Approval Grant is unavailable")
    return _approval_grant(record)


def lock_external_effect(
    connection: Connection[tuple[Any, ...]], step_id: UUID
) -> DurableExternalEffect | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT logical_effect_id, workflow_id, step_id, approval_grant_id, "
            "effect_fingerprint, provider_idempotency_key, dispatch_attempt_id, "
            "dispatch_attempt_number, certainty "
            "FROM example_insurance.external_effects WHERE step_id = %s FOR UPDATE",
            (step_id,),
        ).fetchone()
    if record is None:
        return None
    return _external_effect(record)


def lock_dispatch_authority(
    connection: Connection[tuple[Any, ...]], claim: DispatchClaim
) -> LockedDispatchAuthority:
    guard = KernelControl(connection).guard_current_attempt(
        GuardCurrentAttempt(
            instance_id=claim.instance_id,
            step_id=claim.step_id,
            attempt_id=claim.attempt_id,
            attempt_number=claim.attempt_number,
        )
    )
    workflow = _lock_workflow_authority(connection, claim.instance_id)
    grant = _lock_approval_grant(connection, workflow.workflow_id, claim.step_id)
    durable_claim = _load_durable_dispatch_claim(connection, claim.attempt_id)
    effect = lock_external_effect(connection, claim.step_id)
    return LockedDispatchAuthority(
        guard=guard,
        workflow=workflow,
        grant=grant,
        durable_claim=durable_claim,
        effect=effect,
    )


def commit_dispatch_fence(
    connection: Connection[tuple[Any, ...]],
    *,
    workflow: DurableWorkflowAuthority,
    grant: DurableApprovalGrant,
    claim: DispatchClaim,
    effect: DurableExternalEffect | None,
    effect_id: UUID,
) -> str:
    if effect is None:
        provider_key = str(effect_id)
        connection.execute(
            "INSERT INTO example_insurance.external_effects "
            "(logical_effect_id, workflow_id, step_id, approval_grant_id, "
            "effect_fingerprint, provider_idempotency_key, dispatch_attempt_id, "
            "dispatch_attempt_number, certainty) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'dispatching')",
            (
                effect_id,
                workflow.workflow_id,
                claim.step_id,
                grant.approval_grant_id,
                grant.effect_fingerprint,
                provider_key,
                claim.attempt_id,
                claim.attempt_number,
            ),
        )
        mark_grant_consumed(connection, grant.approval_grant_id)
        return provider_key
    connection.execute(
        "UPDATE example_insurance.external_effects SET dispatch_attempt_id = %s, "
        "dispatch_attempt_number = %s, certainty = 'dispatching', "
        "updated_at = clock_timestamp() WHERE logical_effect_id = %s",
        (claim.attempt_id, claim.attempt_number, effect_id),
    )
    return effect.provider_idempotency_key


def lock_reconciliation_target(
    connection: Connection[tuple[Any, ...]], step_id: UUID
) -> ReconciliationTarget:
    step = read_step(connection, step_id)
    if step is None:
        raise StateConflict("Reconciliation Step input is unavailable")
    try:
        logical_effect_id = UUID(str(step.input["logical_effect_id"]))
        effect_step_id = UUID(str(step.input["effect_step_id"]))
        basis_attempt_id = UUID(str(step.input["basis_attempt_id"]))
    except (KeyError, ValueError) as error:
        raise StateConflict("Reconciliation Step input is unavailable") from error
    basis_attempt = read_attempt(connection, basis_attempt_id)
    if basis_attempt is None:
        raise StateConflict("Reconciliation basis Attempt is unavailable")
    effect = lock_external_effect(connection, effect_step_id)
    if effect is None or effect.logical_effect_id != logical_effect_id:
        raise StateConflict("Reconciliation target External Effect is unavailable")
    return ReconciliationTarget(
        effect=effect,
        effect_step_id=effect_step_id,
        basis_attempt_id=basis_attempt_id,
        effect_attempt_number=basis_attempt.attempt_number,
    )


def record_effect_observation(
    connection: Connection[tuple[Any, ...]],
    *,
    logical_effect_id: UUID,
    attempt_id: UUID,
    classification: EffectObservation,
    source: EffectEvidenceSource,
    provider_request_id: str | None,
) -> UUID:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "UPDATE example_insurance.external_effects SET certainty = %s, "
            "updated_at = clock_timestamp() WHERE logical_effect_id = %s "
            "RETURNING workflow_id",
            (classification, logical_effect_id),
        ).fetchone()
    if record is None:
        raise StateConflict("External Effect disappeared while recording evidence")
    _record_effect_evidence(
        connection,
        logical_effect_id=logical_effect_id,
        attempt_id=attempt_id,
        classification=classification,
        source=source,
        provider_request_id=provider_request_id,
    )
    return UUID(str(record["workflow_id"]))


__all__ = [
    "EffectEvidenceSource",
    "LockedDispatchAuthority",
    "ReconciliationTarget",
    "commit_dispatch_fence",
    "effect_evidence_source",
    "lock_dispatch_authority",
    "lock_external_effect",
    "lock_reconciliation_target",
    "record_effect_observation",
    "requested_dispatch_claim",
]
