"""Transaction-bound persistence for renewal External Effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.commands import StateConflict
from openmagic_runtime.kernel.work import ClaimedAttempt
from psycopg import Connection

from example_insurance.renewal_effect_policy import (
    DispatchClaim,
    DurableApprovalGrant,
    DurableExternalEffect,
    DurableWorkflowAuthority,
    EffectObservation,
    effect_certainty,
)
from example_insurance.renewal_effects import RenewalEmailEffect
from example_insurance.renewal_grant_records import mark_grant_consumed
from example_insurance.renewal_lifecycle_policy import workflow_lifecycle
from example_insurance.renewal_records import EffectEvidenceSource, record_effect_evidence


@dataclass(frozen=True)
class ReconciliationTarget:
    effect: DurableExternalEffect
    effect_step_id: UUID
    basis_attempt_id: UUID
    effect_attempt_number: int


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


def lock_workflow_authority(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> DurableWorkflowAuthority:
    row = connection.execute(
        "SELECT workflow_id, instance_id, lifecycle, authority_revoked_at IS NOT NULL "
        "FROM example_insurance.renewal_workflows WHERE instance_id = %s FOR UPDATE",
        (instance_id,),
    ).fetchone()
    if row is None:
        raise StateConflict("Renewal Workflow is unavailable")
    return DurableWorkflowAuthority(
        workflow_id=UUID(str(row[0])),
        instance_id=UUID(str(row[1])),
        lifecycle=workflow_lifecycle(row[2]),
        authority_revoked=bool(row[3]),
    )


def load_durable_dispatch_claim(
    connection: Connection[tuple[Any, ...]], attempt_id: UUID
) -> DispatchClaim:
    row = connection.execute(
        "SELECT a.instance_id, a.step_id, a.attempt_number, a.worker_id, "
        "s.template_key, s.input FROM openmagic_runtime.attempts a "
        "JOIN openmagic_runtime.steps s ON s.step_id = a.step_id "
        "WHERE a.attempt_id = %s",
        (attempt_id,),
    ).fetchone()
    if row is None:
        raise StateConflict("Durable dispatch Attempt is unavailable")
    return _claim_from_input(
        instance_id=UUID(str(row[0])),
        step_id=UUID(str(row[1])),
        attempt_id=attempt_id,
        attempt_number=int(row[2]),
        worker_id=str(row[3]),
        template_key=str(row[4]),
        step_input=dict(row[5]),
    )


def lock_approval_grant(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID, step_id: UUID
) -> DurableApprovalGrant:
    row = connection.execute(
        "SELECT approval_grant_id, workflow_id, step_id, effect_fingerprint, "
        "invalidated_at IS NOT NULL, consumed_at IS NOT NULL "
        "FROM example_insurance.approval_grants WHERE workflow_id = %s AND step_id = %s "
        "FOR UPDATE",
        (workflow_id, step_id),
    ).fetchone()
    if row is None:
        raise StateConflict("Exact Approval Grant is unavailable")
    return DurableApprovalGrant(
        approval_grant_id=UUID(str(row[0])),
        workflow_id=UUID(str(row[1])),
        step_id=UUID(str(row[2])),
        effect_fingerprint=str(row[3]),
        invalidated=bool(row[4]),
        consumed=bool(row[5]),
    )


def lock_external_effect(
    connection: Connection[tuple[Any, ...]], step_id: UUID
) -> DurableExternalEffect | None:
    row = connection.execute(
        "SELECT logical_effect_id, workflow_id, step_id, approval_grant_id, "
        "effect_fingerprint, provider_idempotency_key, dispatch_attempt_id, "
        "dispatch_attempt_number, certainty "
        "FROM example_insurance.external_effects WHERE step_id = %s FOR UPDATE",
        (step_id,),
    ).fetchone()
    if row is None:
        return None
    return DurableExternalEffect(
        logical_effect_id=UUID(str(row[0])),
        workflow_id=UUID(str(row[1])),
        step_id=UUID(str(row[2])),
        approval_grant_id=UUID(str(row[3])),
        effect_fingerprint=str(row[4]),
        provider_idempotency_key=str(row[5]),
        dispatch_attempt_id=UUID(str(row[6])),
        dispatch_attempt_number=int(row[7]),
        certainty=effect_certainty(row[8]),
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
    row = connection.execute(
        "SELECT (s.input->>'logical_effect_id')::uuid, "
        "(s.input->>'effect_step_id')::uuid, (s.input->>'basis_attempt_id')::uuid, "
        "a.attempt_number FROM openmagic_runtime.steps s "
        "JOIN openmagic_runtime.attempts a ON a.attempt_id = "
        "(s.input->>'basis_attempt_id')::uuid "
        "WHERE s.step_id = %s",
        (step_id,),
    ).fetchone()
    if row is None:
        raise StateConflict("Reconciliation Step input is unavailable")
    effect_step_id = UUID(str(row[1]))
    effect = lock_external_effect(connection, effect_step_id)
    if effect is None or effect.logical_effect_id != UUID(str(row[0])):
        raise StateConflict("Reconciliation target External Effect is unavailable")
    return ReconciliationTarget(
        effect=effect,
        effect_step_id=effect_step_id,
        basis_attempt_id=UUID(str(row[2])),
        effect_attempt_number=int(row[3]),
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
    workflow = connection.execute(
        "UPDATE example_insurance.external_effects SET certainty = %s, "
        "updated_at = clock_timestamp() WHERE logical_effect_id = %s "
        "RETURNING workflow_id",
        (classification, logical_effect_id),
    ).fetchone()
    if workflow is None:
        raise StateConflict("External Effect disappeared while recording evidence")
    record_effect_evidence(
        connection,
        logical_effect_id=logical_effect_id,
        attempt_id=attempt_id,
        classification=classification,
        source=source,
        provider_request_id=provider_request_id,
    )
    return UUID(str(workflow[0]))


__all__ = [
    "ReconciliationTarget",
    "commit_dispatch_fence",
    "load_durable_dispatch_claim",
    "lock_approval_grant",
    "lock_external_effect",
    "lock_reconciliation_target",
    "lock_workflow_authority",
    "record_effect_observation",
    "requested_dispatch_claim",
]
