"""Transactional Workflow Job claim and Run result protocol."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import (
    ClaimWorkflowJobCommand,
    CommittedRunResult,
    ReportRunResultCommand,
    RunResult,
    WorkflowCommandContext,
    WorkflowExecutionPacket,
)
from .database import WorkflowDatabase
from .errors import (
    RunResultConflictError,
    StaleRunError,
    WorkflowAuthorizationError,
)
from .models import (
    NotificationRow,
    WorkflowEventRow,
    WorkflowJobDependencyRow,
    WorkflowJobRow,
    WorkflowJobRunRow,
    WorkflowRow,
)
from .registry import ExecutionStrategy, WorkflowKindRegistry

CurrentBrokerAuthority = Callable[
    [AsyncSession, WorkflowCommandContext, WorkflowRow], Awaitable[bool]
]


class WorkflowExecutionProtocol:
    """Own Job claims and Run result transitions behind the Control Plane."""

    def __init__(
        self,
        *,
        database: WorkflowDatabase,
        registry: WorkflowKindRegistry,
        has_current_broker_authority: CurrentBrokerAuthority,
    ) -> None:
        self._database = database
        self._registry = registry
        self._has_current_broker_authority = has_current_broker_authority

    async def claim_job(
        self,
        command: ClaimWorkflowJobCommand,
    ) -> WorkflowExecutionPacket | None:
        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            await self._recover_expired_runs(session, now)
            candidates = (
                await session.execute(
                    sa.select(
                        WorkflowJobRow.id,
                        WorkflowJobRow.workflow_id,
                        WorkflowJobRow.kind,
                    )
                    .where(
                        WorkflowJobRow.status == "queued",
                        WorkflowJobRow.available_at <= now,
                        WorkflowJobRow.attempts < WorkflowJobRow.max_attempts,
                    )
                    .order_by(
                        WorkflowJobRow.available_at,
                        WorkflowJobRow.created_at,
                        WorkflowJobRow.id,
                    )
                    .limit(20)
                )
            ).all()
            for job_id, workflow_id, job_kind in candidates:
                contract = self._registry.job_contract(job_kind)
                if contract.executor_key not in command.executor_keys:
                    continue
                workflow = await session.scalar(
                    sa.select(WorkflowRow).where(WorkflowRow.id == workflow_id).with_for_update()
                )
                if workflow is None or workflow.status != "active":
                    continue
                job = await session.scalar(
                    sa.select(WorkflowJobRow)
                    .where(
                        WorkflowJobRow.workflow_id == workflow_id,
                        WorkflowJobRow.id == job_id,
                    )
                    .with_for_update()
                )
                if job is None or not await self._job_is_eligible(session, job, now):
                    continue
                if not await self._job_has_current_authority(session, workflow, job):
                    continue

                execution_input = self._registry.validate_job_input(job.kind, job.input)
                runtime_instance_id = (
                    uuid4()
                    if contract.execution_strategy == ExecutionStrategy.FRESH_EXECUTION_AGENT
                    else None
                )
                lease_expires_at = now + command.lease_duration
                run = WorkflowJobRunRow(
                    id=uuid4(),
                    workflow_id=workflow.id,
                    job_id=job.id,
                    status="running",
                    worker_id=command.worker_id,
                    lease_expires_at=lease_expires_at,
                    runtime_instance_id=runtime_instance_id,
                    application_build=command.application_build,
                )
                job.attempts += 1
                job.status = "running"
                session.add(run)
                await session.flush()
                session.add(
                    WorkflowEventRow(
                        workflow_id=workflow.id,
                        job_id=job.id,
                        run_id=run.id,
                        event_type="run_started",
                        actor_type="worker",
                        actor_id=command.worker_id,
                        cause_type="job",
                        cause_id=str(job.id),
                        data={"attempt": job.attempts},
                    )
                )
                await session.flush()
                return WorkflowExecutionPacket(
                    workflow_id=workflow.id,
                    job_id=job.id,
                    run_id=run.id,
                    job_kind=job.kind,
                    execution_strategy=contract.execution_strategy.value,
                    executor_key=contract.executor_key,
                    input=execution_input,
                    runtime_instance_id=runtime_instance_id,
                    lease_expires_at=lease_expires_at,
                )
        return None

    @staticmethod
    async def _recover_expired_runs(session: AsyncSession, now: datetime) -> None:
        expired = (
            await session.execute(
                sa.select(
                    WorkflowJobRunRow.id,
                    WorkflowJobRunRow.workflow_id,
                    WorkflowJobRunRow.job_id,
                )
                .where(
                    WorkflowJobRunRow.status == "running",
                    WorkflowJobRunRow.lease_expires_at < now,
                )
                .order_by(WorkflowJobRunRow.lease_expires_at, WorkflowJobRunRow.id)
                .limit(20)
            )
        ).all()
        for run_id, workflow_id, job_id in expired:
            workflow = await session.scalar(
                sa.select(WorkflowRow).where(WorkflowRow.id == workflow_id).with_for_update()
            )
            if workflow is None:
                continue
            job = await session.scalar(
                sa.select(WorkflowJobRow)
                .where(WorkflowJobRow.workflow_id == workflow_id, WorkflowJobRow.id == job_id)
                .with_for_update()
            )
            run = await session.scalar(
                sa.select(WorkflowJobRunRow)
                .where(
                    WorkflowJobRunRow.workflow_id == workflow_id,
                    WorkflowJobRunRow.job_id == job_id,
                    WorkflowJobRunRow.id == run_id,
                )
                .with_for_update()
            )
            if (
                job is None
                or run is None
                or job.status != "running"
                or run.status != "running"
                or run.lease_expires_at >= now
            ):
                continue
            dispatch_started = await session.scalar(
                sa.select(WorkflowEventRow.id)
                .where(
                    WorkflowEventRow.workflow_id == workflow_id,
                    WorkflowEventRow.job_id == job_id,
                    WorkflowEventRow.event_type == "external_effect_dispatch_started",
                )
                .limit(1)
            )
            run.status = "abandoned"
            run.finished_at = now
            if dispatch_started is not None:
                job.status = "waiting"
            elif job.attempts < job.max_attempts:
                job.status = "queued"
                job.available_at = now
            else:
                job.status = "failed"
            WorkflowExecutionProtocol._append_run_event(
                session,
                workflow,
                job,
                run,
                "run_abandoned",
                {"reason": "lease_expired"},
            )
        await session.flush()

    async def report_run_result(
        self,
        command: ReportRunResultCommand,
    ) -> CommittedRunResult:
        async with self._database.transaction() as session:
            locator = (
                await session.execute(
                    sa.select(WorkflowJobRunRow.workflow_id, WorkflowJobRunRow.job_id).where(
                        WorkflowJobRunRow.id == command.run_id
                    )
                )
            ).one_or_none()
            if locator is None:
                raise StaleRunError("Run does not exist")
            workflow_id, job_id = locator
            workflow = await session.scalar(
                sa.select(WorkflowRow).where(WorkflowRow.id == workflow_id).with_for_update()
            )
            job = await session.scalar(
                sa.select(WorkflowJobRow)
                .where(WorkflowJobRow.workflow_id == workflow_id, WorkflowJobRow.id == job_id)
                .with_for_update()
            )
            run = await session.scalar(
                sa.select(WorkflowJobRunRow)
                .where(
                    WorkflowJobRunRow.workflow_id == workflow_id,
                    WorkflowJobRunRow.job_id == job_id,
                    WorkflowJobRunRow.id == command.run_id,
                )
                .with_for_update()
            )
            if workflow is None or job is None or run is None:
                raise StaleRunError("Run aggregate no longer exists")

            normalized_result = command.result.model_dump(mode="json")
            if run.result is not None:
                if run.result != normalized_result:
                    raise RunResultConflictError("Run already has a different result")
                return self._committed_result(workflow, job, run)
            if (
                workflow.status != "active"
                or job.status != "running"
                or run.status != "running"
                or run.lease_expires_at < datetime.now(UTC)
            ):
                raise StaleRunError("Run no longer has Execution Authority")

            now = datetime.now(UTC)
            run.result = normalized_result
            run.finished_at = now
            if command.result.outcome == "succeeded":
                output = self._registry.validate_success_data(job.kind, command.result.data)
                if job.output is not None:
                    raise RunResultConflictError("Job output was already published")
                run.status = "succeeded"
                job.output = output
                job.status = "succeeded"
                await self._record_success(session, workflow, job, run)
            elif command.result.outcome == "uncertain":
                run.status = "failed"
                job.status = "waiting"
                self._append_run_event(session, workflow, job, run, "run_outcome_uncertain", {})
            else:
                run.status = "failed"
                contract = self._registry.job_contract(job.kind)
                error_code = (
                    command.result.error.get("code") if command.result.error is not None else None
                )
                retry_scheduled = (
                    isinstance(error_code, str)
                    and error_code in contract.retryable_error_codes
                    and job.attempts < job.max_attempts
                )
                if retry_scheduled:
                    job.status = "queued"
                    job.available_at = now + contract.retry_backoff * job.attempts
                else:
                    job.status = "failed"
                self._append_run_event(
                    session,
                    workflow,
                    job,
                    run,
                    "run_failed",
                    {"retry_scheduled": retry_scheduled},
                )
            await session.flush()
            return self._committed_result(workflow, job, run)

    async def _job_is_eligible(
        self,
        session: AsyncSession,
        job: WorkflowJobRow,
        now: datetime,
    ) -> bool:
        if job.status != "queued" or job.available_at > now or job.attempts >= job.max_attempts:
            return False
        unresolved_dependency = await session.scalar(
            sa.select(WorkflowJobDependencyRow.job_id)
            .join(
                WorkflowJobRow,
                sa.and_(
                    WorkflowJobRow.workflow_id == WorkflowJobDependencyRow.workflow_id,
                    WorkflowJobRow.id == WorkflowJobDependencyRow.depends_on_job_id,
                ),
            )
            .where(
                WorkflowJobDependencyRow.workflow_id == job.workflow_id,
                WorkflowJobDependencyRow.job_id == job.id,
                WorkflowJobRow.status != "succeeded",
            )
            .limit(1)
        )
        if unresolved_dependency is not None:
            return False
        if self._registry.requires_approval(job.kind):
            approval = await session.scalar(
                sa.select(WorkflowEventRow.id)
                .where(
                    WorkflowEventRow.workflow_id == job.workflow_id,
                    WorkflowEventRow.job_id == job.id,
                    WorkflowEventRow.event_type == "approval_granted",
                )
                .limit(1)
            )
            if approval is None:
                return False
        dispatched = await session.scalar(
            sa.select(WorkflowEventRow.id)
            .where(
                WorkflowEventRow.workflow_id == job.workflow_id,
                WorkflowEventRow.job_id == job.id,
                WorkflowEventRow.event_type == "external_effect_dispatch_started",
            )
            .limit(1)
        )
        return dispatched is None

    async def _job_has_current_authority(
        self,
        session: AsyncSession,
        workflow: WorkflowRow,
        job: WorkflowJobRow,
    ) -> bool:
        actor_party_id = await self._proposal_actor(session, workflow.id)
        if actor_party_id is None:
            return False
        return await self._has_current_broker_authority(
            session,
            WorkflowCommandContext(
                actor_party_id=actor_party_id,
                organization_party_id=workflow.organization_party_id,
                cause_type="message",
                cause_id=f"job-claim:{job.id}",
            ),
            workflow,
        )

    async def _record_success(
        self,
        session: AsyncSession,
        workflow: WorkflowRow,
        job: WorkflowJobRow,
        run: WorkflowJobRunRow,
    ) -> None:
        contract = self._registry.job_contract(job.kind)
        event = WorkflowEventRow(
            workflow_id=workflow.id,
            job_id=job.id,
            run_id=run.id,
            event_type=contract.success_event_type,
            actor_type="run",
            actor_id=str(run.id),
            cause_type="job",
            cause_id=str(job.id),
            data={"outcome": "succeeded"},
        )
        session.add(event)
        await session.flush()
        if contract.success_notification_kind is None:
            return
        broker_party_id = await self._proposal_actor(session, workflow.id)
        if broker_party_id is None or not await self._job_has_current_authority(
            session, workflow, job
        ):
            raise WorkflowAuthorizationError("Workflow has no current Broker destination")
        session.add(
            NotificationRow(
                workflow_id=workflow.id,
                workflow_event_id=event.id,
                kind=contract.success_notification_kind,
                destination_type="party",
                destination_id=str(broker_party_id),
                status="queued",
                max_attempts=3,
            )
        )

    @staticmethod
    async def _proposal_actor(session: AsyncSession, workflow_id: UUID) -> UUID | None:
        actor_id = await session.scalar(
            sa.select(WorkflowEventRow.actor_id)
            .where(
                WorkflowEventRow.workflow_id == workflow_id,
                WorkflowEventRow.event_type == "workflow_jobs_proposed",
                WorkflowEventRow.actor_type == "party",
            )
            .limit(1)
        )
        if actor_id is None:
            return None
        try:
            return UUID(actor_id)
        except ValueError:
            return None

    @staticmethod
    def _append_run_event(
        session: AsyncSession,
        workflow: WorkflowRow,
        job: WorkflowJobRow,
        run: WorkflowJobRunRow,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        session.add(
            WorkflowEventRow(
                workflow_id=workflow.id,
                job_id=job.id,
                run_id=run.id,
                event_type=event_type,
                actor_type="run",
                actor_id=str(run.id),
                cause_type="job",
                cause_id=str(job.id),
                data=data,
            )
        )

    @staticmethod
    def _committed_result(
        workflow: WorkflowRow,
        job: WorkflowJobRow,
        run: WorkflowJobRunRow,
    ) -> CommittedRunResult:
        if run.result is None:
            raise StaleRunError("Run has no committed result")
        return CommittedRunResult(
            workflow_id=workflow.id,
            job_id=job.id,
            run_id=run.id,
            run_status=run.status,
            job_status=job.status,
            result=RunResult.model_validate(run.result),
        )
