"""Transaction-bound persistence for evidence-backed renewal completion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection

from example_insurance.renewal_completion_policy import (
    CompletionEffectFact,
    CompletionStepFact,
    completion_step_state,
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


def lock_completion_snapshot(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> CompletionSnapshot | None:
    workflow = connection.execute(
        "SELECT instance_id, lifecycle FROM example_insurance.renewal_workflows "
        "WHERE workflow_id = %s FOR UPDATE",
        (workflow_id,),
    ).fetchone()
    if workflow is None:
        return None
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
    return CompletionSnapshot(
        instance_id=UUID(str(workflow[0])),
        lifecycle=workflow_lifecycle(workflow[1]),
        steps=tuple(
            CompletionStepFact(completion_step_state(row[0]), bool(row[1])) for row in steps
        ),
        effects=tuple(
            CompletionEffectFact(effect_certainty(row[0]), bool(row[1])) for row in effects
        ),
    )


__all__ = ["CompletionSnapshot", "lock_completion_snapshot"]
