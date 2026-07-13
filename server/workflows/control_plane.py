"""Deterministic commands and read traces for one Workflow aggregate."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .approval_protocol import WorkflowApprovalProtocol
from .authority import WorkflowAuthority, WorkflowAuthorizationScope
from .contracts import (
    AcknowledgeNotificationCommand,
    ApprovalGrant,
    ApproveWorkflowJobCommand,
    AuthorityRevocationResult,
    BeginExternalEffectDispatchCommand,
    CancelWorkflowCommand,
    CancelWorkflowResult,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    CommittedRunResult,
    CreateWorkflowCommand,
    NotificationAudienceContext,
    NotificationDeliveryPacket,
    NotificationPresentationContext,
    NotificationStatusContext,
    ProposeWorkflowJobsCommand,
    ProposeWorkflowWorkCommand,
    RecordInteractionCauseCommand,
    ReportNotificationFailureCommand,
    ReportRunResultCommand,
    RevokeWorkflowAuthorityCommand,
    WorkflowCommandContext,
    WorkflowExecutionPacket,
    WorkflowJobProposal,
    WorkflowProposal,
    WorkflowTrace,
    WorkflowTraceEvent,
    WorkflowTraceJob,
    WorkflowTraceNotification,
    WorkflowTraceRun,
    WorkflowTraceWorkflow,
)
from .database import WorkflowDatabase
from .email_effects import EmailSendDispatchV1
from .errors import (
    InvalidWorkflowProposalError,
    WorkflowAuthorizationError,
    WorkflowLifecycleError,
    WorkflowNotFoundError,
)
from .execution_protocol import WorkflowExecutionProtocol
from .external_effect_protocol import WorkflowExternalEffectProtocol
from .identity_models import (
    OrganizationMembershipRow,
    PartyIdentifierRow,
    PartyRow,
    WorkflowParticipantRoleRow,
    WorkflowParticipantRow,
)
from .interaction_cause_protocol import WorkflowInteractionCauseProtocol
from .lifecycle_protocol import WorkflowLifecycleProtocol
from .models import (
    NotificationRow,
    WorkflowEventRow,
    WorkflowJobDependencyRow,
    WorkflowJobRow,
    WorkflowJobRunRow,
    WorkflowRow,
)
from .notification_protocol import WorkflowNotificationProtocol
from .proposal_protocol import WorkflowProposalProtocol
from .registry import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    DraftRenewalEmailInput,
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
        notification_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._registry = registry
        self._authority = authority
        self._causes = WorkflowInteractionCauseProtocol(database)
        self._proposals = WorkflowProposalProtocol(registry)
        self._execution = WorkflowExecutionProtocol(
            database=database,
            registry=registry,
            has_current_broker_authority=self._has_current_broker_authority,
        )
        self._approval = WorkflowApprovalProtocol(
            database,
            self._has_current_broker_authority,
            self._causes,
        )
        self._external_effects = WorkflowExternalEffectProtocol(
            database,
            registry,
            self._has_current_broker_authority,
        )
        self._lifecycle = WorkflowLifecycleProtocol(
            database,
            self._has_current_broker_authority,
        )
        self._notification_delivery = WorkflowNotificationProtocol(
            database,
            self._has_current_broker_authority,
            notification_clock,
        )

    async def create_workflow(self, command: CreateWorkflowCommand) -> WorkflowTrace:
        self._require_party_proposable_jobs(command.proposal.jobs)
        validated = self._registry.validate(command.proposal)
        proposal_digest = self._proposals.workflow_digest(validated)
        if not await self._authority.can_create_workflow(command.context, validated.kind):
            raise WorkflowAuthorizationError("Party cannot create this Workflow Kind")

        workflow_id = uuid4()

        async with self._database.transaction() as session:
            await self._causes.require(session, command.context)
            existing_proposal = await self._proposals.event_for_cause(session, command.context)
            if existing_proposal is not None:
                existing_workflow = await session.get(WorkflowRow, existing_proposal.workflow_id)
                if existing_workflow is None or not self._proposals.replays(
                    existing_proposal,
                    command.context,
                    proposal_digest,
                    command.context.organization_party_id,
                ):
                    raise WorkflowLifecycleError("Workflow proposal Cause was already used")
                return await self._proposals.read_receipt(
                    session,
                    existing_workflow,
                    existing_proposal,
                )
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

            proposal_event = await self._proposals.append_graph(
                session,
                workflow,
                validated,
                command.context,
                proposal_digest=proposal_digest,
            )
            trace = await self._proposals.read_receipt(session, workflow, proposal_event)

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
            self._require_party_proposable_jobs(command.jobs)
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
            proposal_digest = self._proposals.job_graph_digest(validated)
            await self._causes.require(session, command.context)
            existing_proposal = await self._proposals.event_for_cause(session, command.context)
            if existing_proposal is not None:
                if existing_proposal.workflow_id != workflow.id or not self._proposals.replays(
                    existing_proposal,
                    command.context,
                    proposal_digest,
                    workflow.organization_party_id,
                ):
                    raise WorkflowLifecycleError("Workflow proposal Cause was already used")
                return await self._proposals.read_receipt(session, workflow, existing_proposal)
            if workflow.status != "active":
                raise WorkflowLifecycleError("Workflow is not active")
            existing_job = await session.scalar(
                sa.select(WorkflowJobRow.id)
                .where(WorkflowJobRow.workflow_id == workflow.id)
                .limit(1)
            )
            if existing_job is not None:
                raise WorkflowLifecycleError("Workflow already has its initial Job graph")

            await self._validate_current_renewal_parties(
                session,
                workflow,
                validated,
                command.context,
            )
            proposal_event = await self._proposals.append_graph(
                session,
                workflow,
                validated,
                command.context,
                proposal_digest=proposal_digest,
            )
            trace = await self._proposals.read_receipt(session, workflow, proposal_event)
        return trace

    async def propose_work(self, command: ProposeWorkflowWorkCommand) -> WorkflowTrace:
        """Resolve trusted facts and compile one business operation into durable Jobs."""

        async with self._database.read_transaction() as session:
            workflow = await session.get(WorkflowRow, command.workflow_id)
            if workflow is None:
                raise WorkflowNotFoundError(str(command.workflow_id))
            resolved_context = command.context.model_copy(
                update={"organization_party_id": workflow.organization_party_id}
            )
            if not await self._has_current_broker_authority(session, resolved_context, workflow):
                raise WorkflowAuthorizationError("Party cannot propose work for this Workflow")
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
                        PartyIdentifierRow.party_id.in_(
                            (resolved_context.actor_party_id, policyholder_id)
                        ),
                        PartyIdentifierRow.kind == "email",
                        PartyIdentifierRow.verified_at.is_not(None),
                        PartyIdentifierRow.revoked_at.is_(None),
                    )
                )
            ).all()
            emails_by_party: dict[UUID, list[str]] = defaultdict(list)
            for party_id, value in identifiers:
                emails_by_party[party_id].append(value)
            broker_emails = emails_by_party[resolved_context.actor_party_id]
            policyholder_emails = emails_by_party[policyholder_id]
            if len(broker_emails) != 1 or len(policyholder_emails) != 1:
                raise WorkflowLifecycleError(
                    "Workflow work requires one verified Broker and Policyholder mailbox"
                )
            renewal_period = workflow.input.get("renewal_period")
            jobs = self._registry.compile_work(
                workflow.kind,
                command.operation,
                {
                    "recipient_name": policyholder_name,
                    "renewal_period": renewal_period,
                    "sender_mailbox": broker_emails[0],
                    "recipient_email": policyholder_emails[0],
                },
            )
        return await self.propose_jobs(
            ProposeWorkflowJobsCommand(
                context=resolved_context,
                workflow_id=command.workflow_id,
                jobs=jobs,
            )
        )

    def _require_party_proposable_jobs(
        self,
        jobs: tuple[WorkflowJobProposal, ...],
    ) -> None:
        if any(job.kind in self._registry.system_job_kinds() for job in jobs):
            raise WorkflowAuthorizationError("Party cannot propose system-authorized work")

    async def claim_job(
        self,
        command: ClaimWorkflowJobCommand,
    ) -> WorkflowExecutionPacket | None:
        return await self._execution.claim_job(command)

    async def approve_job(self, command: ApproveWorkflowJobCommand) -> ApprovalGrant:
        return await self._approval.approve_job(command)

    async def record_interaction_cause(self, command: RecordInteractionCauseCommand) -> None:
        await self._causes.record(command)

    async def begin_external_effect_dispatch(
        self,
        command: BeginExternalEffectDispatchCommand,
    ) -> EmailSendDispatchV1:
        return await self._external_effects.begin_dispatch(command)

    async def cancel_workflow(self, command: CancelWorkflowCommand) -> CancelWorkflowResult:
        return await self._lifecycle.cancel_workflow(command)

    async def revoke_authority(
        self,
        command: RevokeWorkflowAuthorityCommand,
    ) -> AuthorityRevocationResult:
        return await self._lifecycle.revoke_authority(command)

    async def report_run_result(
        self,
        command: ReportRunResultCommand,
    ) -> CommittedRunResult:
        return await self._execution.report_run_result(command)

    async def claim_notification(
        self,
        command: ClaimNotificationCommand,
    ) -> NotificationDeliveryPacket | None:
        return await self._notification_delivery.claim_notification(command)

    async def acknowledge_notification(
        self,
        command: AcknowledgeNotificationCommand,
    ) -> NotificationDeliveryPacket:
        return await self._notification_delivery.acknowledge_notification(command)

    async def report_notification_failure(
        self,
        command: ReportNotificationFailureCommand,
    ) -> NotificationDeliveryPacket:
        return await self._notification_delivery.report_failure(command)

    async def resolve_notification_presentation(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
        worker_id: str,
        delivery_attempt: int,
    ) -> NotificationPresentationContext:
        return await self._notification_delivery.resolve_presentation(
            notification_id,
            workflow_event_id,
            workflow_id,
            worker_id,
            delivery_attempt,
        )

    async def resolve_notification_audience(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
        worker_id: str,
        delivery_attempt: int,
    ) -> NotificationAudienceContext:
        return await self._notification_delivery.resolve_audience(
            notification_id,
            workflow_event_id,
            workflow_id,
            worker_id,
            delivery_attempt,
        )

    async def resolve_notification_status(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
        worker_id: str,
        delivery_attempt: int,
    ) -> NotificationStatusContext:
        return await self._notification_delivery.resolve_status(
            notification_id,
            workflow_event_id,
            workflow_id,
            worker_id,
            delivery_attempt,
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
                    worker_id=run.worker_id,
                    application_build=run.application_build,
                    runtime_instance_id=run.runtime_instance_id,
                    lease_expires_at=run.lease_expires_at,
                    result=run.result,
                    finished_at=run.finished_at,
                )
                for run in runs
            ),
            events=tuple(
                WorkflowTraceEvent(
                    id=event.id,
                    workflow_id=event.workflow_id,
                    job_id=event.job_id,
                    run_id=event.run_id,
                    approval_grant_id=event.approval_grant_id,
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
                    attempts=notification.attempts,
                    max_attempts=notification.max_attempts,
                    available_at=notification.available_at,
                    claimed_by=notification.claimed_by,
                    lease_expires_at=notification.lease_expires_at,
                    delivered_at=notification.delivered_at,
                    delivered_by=notification.delivered_by,
                    last_error=notification.last_error,
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
