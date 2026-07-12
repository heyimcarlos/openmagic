"""Deterministic commands and read traces for one Workflow aggregate."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID, uuid4

import sqlalchemy as sa

from .authority import WorkflowAuthority
from .contracts import (
    CreateWorkflowCommand,
    WorkflowTrace,
    WorkflowTraceEvent,
    WorkflowTraceJob,
    WorkflowTraceNotification,
    WorkflowTraceRun,
    WorkflowTraceWorkflow,
)
from .database import WorkflowDatabase
from .errors import WorkflowAuthorizationError, WorkflowNotFoundError
from .models import (
    NotificationRow,
    WorkflowEventRow,
    WorkflowJobDependencyRow,
    WorkflowJobRow,
    WorkflowJobRunRow,
    WorkflowRow,
)
from .registry import GMAIL_SEND_EMAIL_KIND, WorkflowKindRegistry


class WorkflowControlPlane:
    """Hide validation, authorization, transactions, and storage behind commands."""

    def __init__(
        self,
        *,
        database: WorkflowDatabase,
        registry: WorkflowKindRegistry,
        authority: WorkflowAuthority,
    ) -> None:
        self._database = database
        self._registry = registry
        self._authority = authority

    async def create_workflow(self, command: CreateWorkflowCommand) -> WorkflowTrace:
        validated = self._registry.validate(command.proposal)
        if not await self._authority.can_create_workflow(command.context, validated.kind):
            raise WorkflowAuthorizationError("Party cannot create this Workflow Kind")

        workflow_id = uuid4()
        job_ids = {job.key: uuid4() for job in validated.jobs}
        job_inputs = {
            job.key: self._registry.materialize_job_input(job, job_ids) for job in validated.jobs
        }

        async with self._database.transaction() as session:
            workflow = WorkflowRow(
                id=workflow_id,
                kind=validated.kind,
                objective=validated.objective,
                status="active",
                input=validated.input,
            )
            session.add(workflow)
            await session.flush()

            await session.execute(
                sa.select(WorkflowRow.id).where(WorkflowRow.id == workflow_id).with_for_update()
            )

            job_rows: list[WorkflowJobRow] = []
            for job in validated.jobs:
                status = "waiting" if job.depends_on or job.contract.requires_approval else "queued"
                job_rows.append(
                    WorkflowJobRow(
                        id=job_ids[job.key],
                        workflow_id=workflow_id,
                        kind=job.kind,
                        status=status,
                        attempts=0,
                        max_attempts=job.contract.max_attempts,
                        input=job_inputs[job.key],
                    )
                )
            session.add_all(job_rows)
            await session.flush()

            dependencies = [
                WorkflowJobDependencyRow(
                    workflow_id=workflow_id,
                    job_id=job_ids[job.key],
                    depends_on_job_id=job_ids[dependency],
                )
                for job in validated.jobs
                for dependency in job.depends_on
            ]
            session.add_all(dependencies)
            session.add(
                WorkflowEventRow(
                    workflow_id=workflow_id,
                    event_type="workflow_jobs_proposed",
                    actor_type="party",
                    actor_id=str(command.context.actor_party_id),
                    cause_type=command.context.cause_type,
                    cause_id=command.context.cause_id,
                    data={"job_ids": [str(job_ids[job.key]) for job in validated.jobs]},
                )
            )

        return await self.read_workflow_trace(workflow_id)

    async def read_workflow_trace(self, workflow_id: UUID) -> WorkflowTrace:
        async with self._database.session() as session:
            workflow = await session.get(WorkflowRow, workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError(str(workflow_id))

            jobs = (
                await session.scalars(
                    sa.select(WorkflowJobRow)
                    .where(WorkflowJobRow.workflow_id == workflow_id)
                    .order_by(WorkflowJobRow.created_at, WorkflowJobRow.id)
                )
            ).all()
            dependencies = (
                await session.scalars(
                    sa.select(WorkflowJobDependencyRow)
                    .where(WorkflowJobDependencyRow.workflow_id == workflow_id)
                    .order_by(
                        WorkflowJobDependencyRow.job_id,
                        WorkflowJobDependencyRow.depends_on_job_id,
                    )
                )
            ).all()
            runs = (
                await session.scalars(
                    sa.select(WorkflowJobRunRow)
                    .where(WorkflowJobRunRow.workflow_id == workflow_id)
                    .order_by(WorkflowJobRunRow.created_at, WorkflowJobRunRow.id)
                )
            ).all()
            events = (
                await session.scalars(
                    sa.select(WorkflowEventRow)
                    .where(WorkflowEventRow.workflow_id == workflow_id)
                    .order_by(WorkflowEventRow.occurred_at, WorkflowEventRow.id)
                )
            ).all()
            notifications = (
                await session.scalars(
                    sa.select(NotificationRow)
                    .where(NotificationRow.workflow_id == workflow_id)
                    .order_by(NotificationRow.created_at, NotificationRow.id)
                )
            ).all()

        dependencies_by_job: dict[UUID, list[UUID]] = defaultdict(list)
        for dependency in dependencies:
            dependencies_by_job[dependency.job_id].append(dependency.depends_on_job_id)
        status_by_job = {job.id: job.status for job in jobs}
        approved_job_ids = {
            event.job_id
            for event in events
            if event.event_type == "approval_granted" and event.job_id is not None
        }

        job_traces = tuple(
            WorkflowTraceJob(
                id=job.id,
                workflow_id=job.workflow_id,
                kind=job.kind,
                status=job.status,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                available_at=job.available_at,
                input=job.input,
                output=job.output,
                revises_job_id=job.revises_job_id,
                depends_on_job_ids=tuple(dependencies_by_job[job.id]),
                waiting_reasons=self._waiting_reasons(
                    job,
                    dependencies_by_job[job.id],
                    status_by_job,
                    approved_job_ids,
                ),
                created_at=job.created_at,
            )
            for job in jobs
        )
        return WorkflowTrace(
            workflow=WorkflowTraceWorkflow(
                id=workflow.id,
                kind=workflow.kind,
                objective=workflow.objective,
                status=workflow.status,
                input=workflow.input,
                corrects_workflow_id=workflow.corrects_workflow_id,
                created_at=workflow.created_at,
            ),
            jobs=job_traces,
            runs=tuple(
                WorkflowTraceRun(id=run.id, job_id=run.job_id, status=run.status) for run in runs
            ),
            events=tuple(
                WorkflowTraceEvent(
                    id=event.id,
                    workflow_id=event.workflow_id,
                    job_id=event.job_id,
                    run_id=event.run_id,
                    event_type=event.event_type,
                    actor_type=event.actor_type,
                    actor_id=event.actor_id,
                    cause_type=event.cause_type,
                    cause_id=event.cause_id,
                    data=event.data,
                    occurred_at=event.occurred_at,
                )
                for event in events
            ),
            notifications=tuple(
                WorkflowTraceNotification(
                    id=notification.id,
                    workflow_event_id=notification.workflow_event_id,
                    kind=notification.kind,
                    status=notification.status,
                )
                for notification in notifications
            ),
        )

    @staticmethod
    def _waiting_reasons(
        job: WorkflowJobRow,
        dependency_ids: list[UUID],
        status_by_job: dict[UUID, str],
        approved_job_ids: set[UUID],
    ) -> tuple[str, ...]:
        if job.status != "waiting":
            return ()
        unresolved = tuple(
            f"dependency:{dependency_id}"
            for dependency_id in dependency_ids
            if status_by_job[dependency_id] != "succeeded"
        )
        if unresolved:
            return unresolved
        if job.kind == GMAIL_SEND_EMAIL_KIND and job.id not in approved_job_ids:
            return ("exact_approval",)
        return ()
