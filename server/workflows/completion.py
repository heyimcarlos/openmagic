"""Shared deterministic Workflow completion evaluation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .models import WorkflowEventRow, WorkflowJobRow, WorkflowJobRunRow, WorkflowRow
from .registry import (
    WorkflowCompletionJob,
    WorkflowCompletionView,
    WorkflowKindRegistry,
)


class WorkflowCompletionEvaluator:
    """Derive objective satisfaction from current state and durable evidence."""

    def __init__(self, registry: WorkflowKindRegistry) -> None:
        self._registry = registry

    async def complete_if_satisfied(
        self,
        session: AsyncSession,
        *,
        workflow: WorkflowRow,
        completed_job: WorkflowJobRow,
        run_id: UUID | None,
        cause_type: str,
        cause_id: str,
        occurred_at: datetime | None = None,
    ) -> bool:
        if workflow.status != "active":
            return False
        jobs = (
            await session.scalars(
                sa.select(WorkflowJobRow).where(WorkflowJobRow.workflow_id == workflow.id)
            )
        ).all()
        job_statuses = {job.id: job.status for job in jobs}
        runs = (
            await session.scalars(
                sa.select(WorkflowJobRunRow).where(WorkflowJobRunRow.workflow_id == workflow.id)
            )
        ).all()
        dispatched_job_ids = (
            await session.scalars(
                sa.select(WorkflowEventRow.job_id).where(
                    WorkflowEventRow.workflow_id == workflow.id,
                    WorkflowEventRow.event_type == "external_effect_dispatch_started",
                    WorkflowEventRow.approval_grant_id.is_not(None),
                    WorkflowEventRow.job_id.is_not(None),
                )
            )
        ).all()
        view = WorkflowCompletionView(
            jobs=tuple(
                WorkflowCompletionJob(
                    id=job.id,
                    kind=job.kind,
                    status=job.status,
                    revises_job_id=job.revises_job_id,
                )
                for job in jobs
            ),
            uncertain_job_ids=frozenset(
                run.job_id
                for run in runs
                if run.result is not None
                and run.result.get("outcome") == "uncertain"
                and job_statuses.get(run.job_id) != "succeeded"
            ),
            approved_dispatch_job_ids=frozenset(
                job_id for job_id in dispatched_job_ids if job_id is not None
            ),
        )
        if not self._registry.completion_satisfied(workflow.kind, view):
            return False
        workflow.status = "completed"
        session.add(
            WorkflowEventRow(
                workflow_id=workflow.id,
                job_id=completed_job.id,
                run_id=run_id,
                event_type="workflow_completed",
                actor_type="system",
                actor_id="workflow_control_plane",
                cause_type=cause_type,
                cause_id=cause_id,
                data={"objective_satisfied": True},
                occurred_at=occurred_at or datetime.now(UTC),
            )
        )
        await session.flush()
        return True


__all__ = ["WorkflowCompletionEvaluator"]
