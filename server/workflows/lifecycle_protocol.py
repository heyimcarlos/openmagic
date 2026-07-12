"""Terminal Workflow lifecycle transitions and their dispatch races."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import CancelWorkflowCommand, CancelWorkflowResult, WorkflowCommandContext
from .database import WorkflowDatabase
from .errors import WorkflowAuthorizationError, WorkflowLifecycleError
from .models import WorkflowEventRow, WorkflowJobRow, WorkflowJobRunRow, WorkflowRow

CurrentBrokerAuthority = Callable[
    [AsyncSession, WorkflowCommandContext, WorkflowRow], Awaitable[bool]
]


class WorkflowLifecycleProtocol:
    """Own aggregate cancellation behind one Workflow-row serialization lock."""

    def __init__(
        self,
        database: WorkflowDatabase,
        has_current_broker_authority: CurrentBrokerAuthority,
    ) -> None:
        self._database = database
        self._has_current_broker_authority = has_current_broker_authority

    async def cancel_workflow(self, command: CancelWorkflowCommand) -> CancelWorkflowResult:
        async with self._database.transaction() as session:
            workflow = await session.scalar(
                sa.select(WorkflowRow)
                .where(WorkflowRow.id == command.workflow_id)
                .with_for_update()
            )
            if workflow is None:
                raise WorkflowLifecycleError("Workflow does not exist")
            if workflow.status == "cancelled":
                return CancelWorkflowResult(workflow_id=workflow.id, outcome="cancelled")
            if workflow.status == "completed":
                return CancelWorkflowResult(workflow_id=workflow.id, outcome="too_late")
            if not await self._has_current_broker_authority(session, command.context, workflow):
                raise WorkflowAuthorizationError("Party cannot cancel this Workflow")

            dispatch_exists = await session.scalar(
                sa.select(WorkflowEventRow.id)
                .where(
                    WorkflowEventRow.workflow_id == workflow.id,
                    WorkflowEventRow.event_type == "external_effect_dispatch_started",
                )
                .limit(1)
            )
            runs = (
                await session.scalars(
                    sa.select(WorkflowJobRunRow).where(WorkflowJobRunRow.workflow_id == workflow.id)
                )
            ).all()
            uncertain = any(
                run.result is not None and run.result.get("outcome") == "uncertain" for run in runs
            )
            if dispatch_exists is not None or uncertain:
                return CancelWorkflowResult(workflow_id=workflow.id, outcome="too_late")

            approvals = (
                await session.scalars(
                    sa.select(WorkflowEventRow).where(
                        WorkflowEventRow.workflow_id == workflow.id,
                        WorkflowEventRow.event_type == "approval_granted",
                    )
                )
            ).all()
            invalidated_ids = set(
                await session.scalars(
                    sa.select(WorkflowEventRow.approval_grant_id).where(
                        WorkflowEventRow.workflow_id == workflow.id,
                        WorkflowEventRow.event_type == "approval_invalidated",
                        WorkflowEventRow.approval_grant_id.is_not(None),
                    )
                )
            )
            for approval in approvals:
                if approval.id in invalidated_ids:
                    continue
                session.add(
                    WorkflowEventRow(
                        workflow_id=workflow.id,
                        job_id=approval.job_id,
                        approval_grant_id=approval.id,
                        event_type="approval_invalidated",
                        actor_type="party",
                        actor_id=str(command.context.actor_party_id),
                        cause_type=command.context.cause_type,
                        cause_id=command.context.cause_id,
                        data={
                            "reason": "workflow_cancelled",
                            "approval_grant_id": str(approval.id),
                        },
                    )
                )

            now = datetime.now(UTC)
            for run in runs:
                if run.status == "running":
                    run.status = "cancelled"
                    run.finished_at = now
            jobs = (
                await session.scalars(
                    sa.select(WorkflowJobRow).where(WorkflowJobRow.workflow_id == workflow.id)
                )
            ).all()
            for job in jobs:
                if job.status in {"waiting", "queued", "running"}:
                    job.status = "cancelled"
            workflow.status = "cancelled"
            session.add(
                WorkflowEventRow(
                    workflow_id=workflow.id,
                    event_type="workflow_cancelled",
                    actor_type="party",
                    actor_id=str(command.context.actor_party_id),
                    cause_type=command.context.cause_type,
                    cause_id=command.context.cause_id,
                    data={},
                )
            )
            await session.flush()
            return CancelWorkflowResult(workflow_id=workflow.id, outcome="cancelled")


__all__ = ["WorkflowLifecycleProtocol"]
