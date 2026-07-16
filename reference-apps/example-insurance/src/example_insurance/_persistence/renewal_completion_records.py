"""Private transaction-bound persistence for renewal completion."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_runtime.kernel.inspection import KernelTransactionInspection
from psycopg import Connection
from psycopg.rows import dict_row

from example_insurance._persistence.renewal_workflow_records import lock_instance_for_workflow
from example_insurance.renewal_completion_policy import (
    CompletionEffectFact,
    CompletionStepFact,
)
from example_insurance.renewal_effect_policy import effect_certainty
from example_insurance.renewal_lifecycle_policy import (
    WorkflowLifecycle,
    workflow_lifecycle,
)


@dataclass(frozen=True)
class CompletionSnapshot:
    instance_id: UUID
    lifecycle: WorkflowLifecycle
    steps: tuple[CompletionStepFact, ...]
    effects: tuple[CompletionEffectFact, ...]


@dataclass(frozen=True)
class CompletionWorkflow:
    instance_id: UUID
    lifecycle: WorkflowLifecycle

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> CompletionWorkflow:
        return cls(
            instance_id=UUID(str(record["instance_id"])),
            lifecycle=workflow_lifecycle(record["lifecycle"]),
        )


def _effect_fact(record: Mapping[str, Any]) -> CompletionEffectFact:
    return CompletionEffectFact(
        certainty=effect_certainty(record["certainty"]),
        has_applied_evidence=bool(record["has_definite_applied_evidence"]),
    )


def lock_completion_snapshot(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> CompletionSnapshot | None:
    identity = lock_instance_for_workflow(connection, workflow_id)
    if identity is None:
        return None
    with connection.cursor(row_factory=dict_row) as cursor:
        workflow_record = cursor.execute(
            "SELECT instance_id, lifecycle FROM example_insurance.renewal_workflows "
            "WHERE workflow_id = %s FOR UPDATE",
            (workflow_id,),
        ).fetchone()
    if workflow_record is None:
        return None
    workflow = CompletionWorkflow.decode(workflow_record)
    if workflow.instance_id != identity.instance_id:
        raise RuntimeError("Renewal Workflow identity changed while locking")
    steps = KernelTransactionInspection(connection).steps_for_instance(workflow.instance_id)
    with connection.cursor(row_factory=dict_row) as cursor:
        effect_records = cursor.execute(
            "SELECT e.certainty, EXISTS (SELECT 1 FROM "
            "example_insurance.external_effect_evidence v WHERE "
            "v.logical_effect_id = e.logical_effect_id AND v.classification = 'applied' "
            "AND v.source IN ('provider_response', 'provider_lookup')) "
            "AS has_definite_applied_evidence "
            "FROM example_insurance.external_effects e WHERE e.workflow_id = %s",
            (workflow_id,),
        ).fetchall()
    return CompletionSnapshot(
        instance_id=workflow.instance_id,
        lifecycle=workflow.lifecycle,
        steps=tuple(CompletionStepFact(step.state, step.output_recorded) for step in steps),
        effects=tuple(_effect_fact(record) for record in effect_records),
    )


__all__ = ["CompletionSnapshot", "lock_completion_snapshot"]
