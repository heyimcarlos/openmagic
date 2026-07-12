"""Deterministic commands and read traces for one Workflow aggregate."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .authority import WorkflowAuthority, WorkflowAuthorizationScope
from .contracts import (
    AcknowledgeNotificationCommand,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    CommittedRunResult,
    CreateWorkflowCommand,
    NotificationDeliveryPacket,
    ProposeWorkflowJobsCommand,
    ReportRunResultCommand,
    RunResult,
    WorkflowCommandContext,
    WorkflowExecutionPacket,
    WorkflowProposal,
    WorkflowTrace,
    WorkflowTraceEvent,
    WorkflowTraceJob,
    WorkflowTraceNotification,
    WorkflowTraceRun,
    WorkflowTraceWorkflow,
)
from .database import WorkflowDatabase
from .errors import (
    InvalidWorkflowProposalError,
    NotificationLifecycleError,
    RunResultConflictError,
    StaleRunError,
    WorkflowAuthorizationError,
    WorkflowLifecycleError,
    WorkflowNotFoundError,
)
from .identity_models import (
    OrganizationMembershipRow,
    PartyIdentifierRow,
    PartyRow,
    WorkflowParticipantRoleRow,
    WorkflowParticipantRow,
)
from .models import (
    NotificationRow,
    WorkflowEventRow,
    WorkflowJobDependencyRow,
    WorkflowJobRow,
    WorkflowJobRunRow,
    WorkflowRow,
)
from .registry import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    DraftRenewalEmailInput,
    ExecutionStrategy,
    ProposedGmailSendEmailInput,
    ValidatedWorkflowProposal,
    WorkflowKindRegistry,
)


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

        async with self._database.transaction() as session:
            workflow = WorkflowRow(
                id=workflow_id,
                kind=validated.kind,
                objective=validated.objective,
                status="active",
                input=validated.input,
                organization_party_id=command.context.organization_party_id,
            )
            session.add(workflow)
            await session.flush()

            await session.execute(
                sa.select(WorkflowRow.id).where(WorkflowRow.id == workflow_id).with_for_update()
            )
            session.add(
                WorkflowParticipantRow(
                    workflow_id=workflow_id,
                    party_id=command.context.actor_party_id,
                )
            )
            await session.flush()
            session.add(
                WorkflowParticipantRoleRow(
                    workflow_id=workflow_id,
                    party_id=command.context.actor_party_id,
                    role="Broker",
                    granted_at=datetime.now(UTC),
                )
            )

            await self._append_job_graph(session, workflow, validated, command.context)
            trace = await self._read_trace(session, workflow)

        return trace

    async def propose_jobs(self, command: ProposeWorkflowJobsCommand) -> WorkflowTrace:
        async with self._database.transaction() as session:
            workflow = await session.scalar(
                sa.select(WorkflowRow)
                .where(WorkflowRow.id == command.workflow_id)
                .with_for_update()
            )
            if workflow is None:
                raise WorkflowNotFoundError(str(command.workflow_id))
            if not await self._has_current_broker_authority(session, command.context, workflow):
                raise WorkflowAuthorizationError("Party cannot propose work for this Workflow")
            if workflow.status != "active":
                raise WorkflowLifecycleError("Workflow is not active")
            existing_job = await session.scalar(
                sa.select(WorkflowJobRow.id)
                .where(WorkflowJobRow.workflow_id == workflow.id)
                .limit(1)
            )
            if existing_job is not None:
                raise WorkflowLifecycleError("Workflow already has its initial Job graph")

            validated = self._registry.validate(
                WorkflowProposal(
                    kind=workflow.kind,
                    objective=workflow.objective,
                    input=workflow.input,
                    jobs=command.jobs,
                )
            )
            if validated.kind != workflow.kind:
                raise InvalidWorkflowProposalError("Job graph does not match the Workflow Kind")
            await self._validate_current_renewal_parties(
                session,
                workflow,
                validated,
                command.context,
            )
            await self._append_job_graph(session, workflow, validated, command.context)
            trace = await self._read_trace(session, workflow)
        return trace

    async def claim_job(
        self,
        command: ClaimWorkflowJobCommand,
    ) -> WorkflowExecutionPacket | None:
        """Claim one due Job without allowing the Worker to select its executor."""

        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            candidates = (
                await session.execute(
                    sa.select(WorkflowJobRow.id, WorkflowJobRow.workflow_id)
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
            for job_id, workflow_id in candidates:
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

                contract = self._registry.job_contract(job.kind)
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
                    input=execution_input,
                    runtime_instance_id=runtime_instance_id,
                    lease_expires_at=lease_expires_at,
                )
        return None

    async def report_run_result(
        self,
        command: ReportRunResultCommand,
    ) -> CommittedRunResult:
        """Publish one write-once Run Result through current derived authority."""

        async with self._database.transaction() as session:
            run_locator = await session.execute(
                sa.select(WorkflowJobRunRow.workflow_id, WorkflowJobRunRow.job_id).where(
                    WorkflowJobRunRow.id == command.run_id
                )
            )
            locator = run_locator.one_or_none()
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
                session.add(
                    WorkflowEventRow(
                        workflow_id=workflow.id,
                        job_id=job.id,
                        run_id=run.id,
                        event_type="run_outcome_uncertain",
                        actor_type="run",
                        actor_id=str(run.id),
                        cause_type="job",
                        cause_id=str(job.id),
                        data={},
                    )
                )
            else:
                run.status = "failed"
                job.status = "failed"
                session.add(
                    WorkflowEventRow(
                        workflow_id=workflow.id,
                        job_id=job.id,
                        run_id=run.id,
                        event_type="run_failed",
                        actor_type="run",
                        actor_id=str(run.id),
                        cause_type="job",
                        cause_id=str(job.id),
                        data={},
                    )
                )
            await session.flush()
            return self._committed_result(workflow, job, run)

    async def claim_notification(
        self,
        command: ClaimNotificationCommand,
    ) -> NotificationDeliveryPacket | None:
        """Lease one outbox record without loading Workflow content."""

        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            notification = await session.scalar(
                sa.select(NotificationRow)
                .where(
                    NotificationRow.status == "queued",
                    NotificationRow.available_at <= now,
                    NotificationRow.attempts < NotificationRow.max_attempts,
                )
                .order_by(
                    NotificationRow.available_at,
                    NotificationRow.created_at,
                    NotificationRow.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if notification is None:
                return None
            notification.status = "delivering"
            notification.attempts += 1
            notification.claimed_by = command.worker_id
            notification.lease_expires_at = now + command.lease_duration
            await session.flush()
            return NotificationDeliveryPacket(
                notification_id=notification.id,
                workflow_event_id=notification.workflow_event_id,
                workflow_id=notification.workflow_id,
            )

    async def acknowledge_notification(
        self,
        command: AcknowledgeNotificationCommand,
    ) -> NotificationDeliveryPacket:
        """Acknowledge a delivery once, accepting identical post-commit replay."""

        async with self._database.transaction() as session:
            notification = await session.scalar(
                sa.select(NotificationRow)
                .where(NotificationRow.id == command.notification_id)
                .with_for_update()
            )
            if notification is None:
                raise NotificationLifecycleError("Notification does not exist")
            if notification.status == "delivered":
                return self._notification_packet(notification)
            if (
                notification.status != "delivering"
                or notification.claimed_by != command.worker_id
                or notification.lease_expires_at is None
                or notification.lease_expires_at < datetime.now(UTC)
            ):
                raise NotificationLifecycleError("Notification delivery lease is stale")
            notification.status = "delivered"
            notification.claimed_by = None
            notification.lease_expires_at = None
            notification.delivered_at = datetime.now(UTC)
            await session.flush()
            return self._notification_packet(notification)

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

    async def _record_success(
        self,
        session: AsyncSession,
        workflow: WorkflowRow,
        job: WorkflowJobRow,
        run: WorkflowJobRunRow,
    ) -> None:
        event_type = "draft_ready" if job.kind == DRAFT_RENEWAL_EMAIL_KIND else "job_succeeded"
        event = WorkflowEventRow(
            workflow_id=workflow.id,
            job_id=job.id,
            run_id=run.id,
            event_type=event_type,
            actor_type="run",
            actor_id=str(run.id),
            cause_type="job",
            cause_id=str(job.id),
            data={"outcome": "succeeded"},
        )
        session.add(event)
        await session.flush()
        if job.kind != DRAFT_RENEWAL_EMAIL_KIND:
            return
        broker_party_id = await session.scalar(
            sa.select(WorkflowParticipantRoleRow.party_id)
            .where(
                WorkflowParticipantRoleRow.workflow_id == workflow.id,
                WorkflowParticipantRoleRow.role == "Broker",
                WorkflowParticipantRoleRow.revoked_at.is_(None),
            )
            .order_by(WorkflowParticipantRoleRow.granted_at)
            .limit(1)
        )
        if broker_party_id is None:
            raise WorkflowAuthorizationError("Workflow has no current Broker destination")
        session.add(
            NotificationRow(
                workflow_id=workflow.id,
                workflow_event_id=event.id,
                kind="approval_required",
                destination_type="party",
                destination_id=str(broker_party_id),
                status="queued",
                max_attempts=3,
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

    @staticmethod
    def _notification_packet(notification: NotificationRow) -> NotificationDeliveryPacket:
        return NotificationDeliveryPacket(
            notification_id=notification.id,
            workflow_event_id=notification.workflow_event_id,
            workflow_id=notification.workflow_id,
        )

    @staticmethod
    async def _has_current_broker_authority(
        session: AsyncSession,
        context: WorkflowCommandContext,
        workflow: WorkflowRow,
    ) -> bool:
        if context.organization_party_id != workflow.organization_party_id:
            return False
        predicate = sa.and_(
            sa.exists(
                sa.select(PartyIdentifierRow.id).where(
                    PartyIdentifierRow.party_id == context.actor_party_id,
                    PartyIdentifierRow.verified_at.is_not(None),
                    PartyIdentifierRow.revoked_at.is_(None),
                )
            ),
            sa.exists(
                sa.select(OrganizationMembershipRow.id).where(
                    OrganizationMembershipRow.person_party_id == context.actor_party_id,
                    OrganizationMembershipRow.organization_party_id
                    == workflow.organization_party_id,
                    OrganizationMembershipRow.revoked_at.is_(None),
                )
            ),
            sa.exists(
                sa.select(WorkflowParticipantRoleRow.id).where(
                    WorkflowParticipantRoleRow.workflow_id == workflow.id,
                    WorkflowParticipantRoleRow.party_id == context.actor_party_id,
                    WorkflowParticipantRoleRow.role == "Broker",
                    WorkflowParticipantRoleRow.revoked_at.is_(None),
                )
            ),
        )
        return bool(await session.scalar(sa.select(predicate)))

    @staticmethod
    async def _validate_current_renewal_parties(
        session: AsyncSession,
        workflow: WorkflowRow,
        validated: ValidatedWorkflowProposal,
        context: WorkflowCommandContext,
    ) -> None:
        if workflow.kind != "renewal_outreach.v1":
            return
        policyholders = (
            await session.execute(
                sa.select(PartyRow.id, PartyRow.display_name)
                .join(
                    WorkflowParticipantRoleRow,
                    WorkflowParticipantRoleRow.party_id == PartyRow.id,
                )
                .where(
                    WorkflowParticipantRoleRow.workflow_id == workflow.id,
                    WorkflowParticipantRoleRow.role == "Policyholder",
                    WorkflowParticipantRoleRow.revoked_at.is_(None),
                )
            )
        ).all()
        if len(policyholders) != 1:
            raise WorkflowLifecycleError("Workflow must have one current Policyholder")
        policyholder_id, policyholder_name = policyholders[0]
        identifiers = (
            await session.execute(
                sa.select(PartyIdentifierRow.party_id, PartyIdentifierRow.value).where(
                    PartyIdentifierRow.party_id.in_((context.actor_party_id, policyholder_id)),
                    PartyIdentifierRow.kind == "email",
                    PartyIdentifierRow.verified_at.is_not(None),
                    PartyIdentifierRow.revoked_at.is_(None),
                )
            )
        ).all()
        emails_by_party: dict[UUID, set[str]] = defaultdict(set)
        for party_id, value in identifiers:
            emails_by_party[party_id].add(value.casefold())

        draft = next(job for job in validated.jobs if job.kind == DRAFT_RENEWAL_EMAIL_KIND)
        send = next(job for job in validated.jobs if job.kind == GMAIL_SEND_EMAIL_KIND)
        draft_input = DraftRenewalEmailInput.model_validate(draft.proposed_input)
        send_input = ProposedGmailSendEmailInput.model_validate(send.proposed_input)
        if draft_input.recipient_name != policyholder_name:
            raise WorkflowLifecycleError("Draft recipient no longer matches the Policyholder")
        if send_input.sender_mailbox.casefold() not in emails_by_party[context.actor_party_id]:
            raise WorkflowAuthorizationError("Sender mailbox is not authorized for the Broker")
        recipients = {str(recipient).casefold() for recipient in send_input.to}
        if len(recipients) != 1 or not recipients <= emails_by_party[policyholder_id]:
            raise WorkflowLifecycleError("Recipient no longer matches the Policyholder")

    async def _append_job_graph(
        self,
        session: AsyncSession,
        workflow: WorkflowRow,
        validated: ValidatedWorkflowProposal,
        context: WorkflowCommandContext,
    ) -> None:
        job_ids = {job.key: uuid4() for job in validated.jobs}
        job_inputs = {
            job.key: self._registry.materialize_job_input(job, job_ids) for job in validated.jobs
        }
        session.add_all(
            [
                WorkflowJobRow(
                    id=job_ids[job.key],
                    workflow_id=workflow.id,
                    kind=job.kind,
                    status=(
                        "waiting" if job.depends_on or job.contract.requires_approval else "queued"
                    ),
                    attempts=0,
                    max_attempts=job.contract.max_attempts,
                    input=job_inputs[job.key],
                )
                for job in validated.jobs
            ]
        )
        await session.flush()
        session.add_all(
            [
                WorkflowJobDependencyRow(
                    workflow_id=workflow.id,
                    job_id=job_ids[job.key],
                    depends_on_job_id=job_ids[dependency],
                )
                for job in validated.jobs
                for dependency in job.depends_on
            ]
        )
        session.add(
            WorkflowEventRow(
                workflow_id=workflow.id,
                event_type="workflow_jobs_proposed",
                actor_type="party",
                actor_id=str(context.actor_party_id),
                cause_type=context.cause_type,
                cause_id=context.cause_id,
                data={
                    "job_ids": [str(job_ids[job.key]) for job in validated.jobs],
                    "organization_party_id": str(workflow.organization_party_id),
                },
            )
        )
        await session.flush()

    async def read_workflow_trace(
        self,
        workflow_id: UUID,
        context: WorkflowCommandContext,
    ) -> WorkflowTrace:
        async with self._database.read_transaction() as session:
            workflow = await session.get(WorkflowRow, workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError(str(workflow_id))
            creation_event = await session.scalar(
                sa.select(WorkflowEventRow)
                .where(
                    WorkflowEventRow.workflow_id == workflow_id,
                    WorkflowEventRow.event_type == "workflow_jobs_proposed",
                )
                .limit(1)
            )
            if creation_event is None:
                raise WorkflowNotFoundError(str(workflow_id))
            scope = self._authorization_scope(creation_event, workflow_id)
            allowed = await self._authority.can_read_workflow(
                context,
                workflow_id,
                workflow.kind,
                scope,
            )
            if not allowed:
                raise WorkflowNotFoundError(str(workflow_id))
            return await self._read_trace(session, workflow)

    async def _read_trace(
        self,
        session: AsyncSession,
        workflow: WorkflowRow,
    ) -> WorkflowTrace:
        workflow_id = workflow.id
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
                WorkflowTraceRun(
                    id=run.id,
                    job_id=run.job_id,
                    status=run.status,
                    runtime_instance_id=run.runtime_instance_id,
                )
                for run in runs
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
    def _authorization_scope(
        creation_event: WorkflowEventRow,
        workflow_id: UUID,
    ) -> WorkflowAuthorizationScope:
        organization_party_id = creation_event.data.get("organization_party_id")
        try:
            return WorkflowAuthorizationScope(
                actor_party_id=UUID(creation_event.actor_id),
                organization_party_id=UUID(str(organization_party_id)),
            )
        except (TypeError, ValueError) as exc:
            raise WorkflowNotFoundError(str(workflow_id)) from exc

    def _waiting_reasons(
        self,
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
        if self._registry.requires_approval(job.kind) and job.id not in approved_job_ids:
            return ("exact_approval",)
        return ()
