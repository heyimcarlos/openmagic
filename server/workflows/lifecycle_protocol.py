"""Terminal Workflow lifecycle transitions and their dispatch races."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .authority import CurrentBrokerAuthority
from .contracts import (
    AuthorityRevocationResult,
    CancelWorkflowCommand,
    CancelWorkflowResult,
    RevokeWorkflowAuthorityCommand,
)
from .database import WorkflowDatabase
from .errors import WorkflowAuthorizationError, WorkflowLifecycleError
from .identity_models import (
    OrganizationMembershipRow,
    PartyIdentifierRow,
    WorkflowParticipantRoleRow,
)
from .models import WorkflowEventRow, WorkflowJobRow, WorkflowJobRunRow, WorkflowRow


class WorkflowLifecycleProtocol:
    """Own aggregate cancellation behind one Workflow-row serialization lock."""

    def __init__(
        self,
        database: WorkflowDatabase,
        has_current_broker_authority: CurrentBrokerAuthority,
    ) -> None:
        self._database = database
        self._has_current_broker_authority = has_current_broker_authority

    async def revoke_authority(
        self,
        command: RevokeWorkflowAuthorityCommand,
    ) -> AuthorityRevocationResult:
        """Serialize authority revocation against dispatch for one Workflow."""

        async with self._database.transaction() as session:
            workflow = await session.scalar(
                sa.select(WorkflowRow)
                .where(WorkflowRow.id == command.workflow_id)
                .with_for_update()
            )
            if workflow is None:
                raise WorkflowLifecycleError("Workflow does not exist")
            if command.subject_party_id != command.context.actor_party_id:
                raise WorkflowAuthorizationError("V0 authority revocation is self-scoped")
            if not await self._has_current_broker_authority(session, command.context, workflow):
                raise WorkflowAuthorizationError("Party cannot revoke Workflow authority")

            now = datetime.now(UTC)
            await self._revoke_authority_fact(session, workflow, command, now)
            approvals = (
                await session.scalars(
                    sa.select(WorkflowEventRow).where(
                        WorkflowEventRow.workflow_id == workflow.id,
                        WorkflowEventRow.event_type == "approval_granted",
                        WorkflowEventRow.actor_id == str(command.subject_party_id),
                    )
                )
            ).all()
            invalidated = 0
            for approval in approvals:
                if approval.job_id is None:
                    continue
                dispatch = await session.scalar(
                    sa.select(WorkflowEventRow.id).where(
                        WorkflowEventRow.workflow_id == workflow.id,
                        WorkflowEventRow.job_id == approval.job_id,
                        WorkflowEventRow.event_type == "external_effect_dispatch_started",
                    )
                )
                prior = await session.scalar(
                    sa.select(WorkflowEventRow.id).where(
                        WorkflowEventRow.workflow_id == workflow.id,
                        WorkflowEventRow.approval_grant_id == approval.id,
                        WorkflowEventRow.event_type == "approval_invalidated",
                    )
                )
                if dispatch is not None or prior is not None:
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
                            "reason": command.reason,
                            "approval_grant_id": str(approval.id),
                        },
                    )
                )
                runs = (
                    await session.scalars(
                        sa.select(WorkflowJobRunRow).where(
                            WorkflowJobRunRow.workflow_id == workflow.id,
                            WorkflowJobRunRow.job_id == approval.job_id,
                            WorkflowJobRunRow.status == "running",
                        )
                    )
                ).all()
                for run in runs:
                    run.status = "cancelled"
                    run.finished_at = now
                job = await session.get(WorkflowJobRow, approval.job_id)
                if job is not None and job.status in {"waiting", "queued", "running"}:
                    job.status = "waiting"
                invalidated += 1
            await session.flush()
            return AuthorityRevocationResult(
                workflow_id=workflow.id,
                reason=command.reason,
                invalidated_grants=invalidated,
            )

    @staticmethod
    async def _revoke_authority_fact(
        session: AsyncSession,
        workflow: WorkflowRow,
        command: RevokeWorkflowAuthorityCommand,
        now: datetime,
    ) -> None:
        if command.reason == "broker_role_revoked":
            row = await session.scalar(
                sa.select(WorkflowParticipantRoleRow)
                .where(
                    WorkflowParticipantRoleRow.workflow_id == workflow.id,
                    WorkflowParticipantRoleRow.party_id == command.subject_party_id,
                    WorkflowParticipantRoleRow.role == "Broker",
                    WorkflowParticipantRoleRow.revoked_at.is_(None),
                )
                .with_for_update()
            )
        elif command.reason == "organization_membership_revoked":
            row = await session.scalar(
                sa.select(OrganizationMembershipRow)
                .where(
                    OrganizationMembershipRow.person_party_id == command.subject_party_id,
                    OrganizationMembershipRow.organization_party_id
                    == workflow.organization_party_id,
                    OrganizationMembershipRow.revoked_at.is_(None),
                )
                .with_for_update()
            )
        else:
            row = await session.scalar(
                sa.select(PartyIdentifierRow)
                .where(
                    PartyIdentifierRow.party_id == command.subject_party_id,
                    PartyIdentifierRow.kind == "email",
                    PartyIdentifierRow.revoked_at.is_(None),
                )
                .with_for_update()
            )
        if row is None:
            raise WorkflowLifecycleError("Authority fact is not currently active")
        row.revoked_at = now

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
