"""Exact Job approval commands serialized through the Workflow aggregate."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import ApprovalGrant, ApproveWorkflowJobCommand, WorkflowCommandContext
from .database import WorkflowDatabase
from .email_effects import fingerprint_email_effect, resolve_email_effect
from .errors import WorkflowAuthorizationError, WorkflowLifecycleError
from .models import WorkflowEventRow, WorkflowJobRow, WorkflowRow
from .registry import GMAIL_SEND_EMAIL_KIND

CurrentBrokerAuthority = Callable[
    [AsyncSession, WorkflowCommandContext, WorkflowRow], Awaitable[bool]
]


class WorkflowApprovalProtocol:
    """Record one immutable Approval Grant for one exact presented effect."""

    def __init__(
        self,
        database: WorkflowDatabase,
        has_current_broker_authority: CurrentBrokerAuthority,
    ) -> None:
        self._database = database
        self._has_current_broker_authority = has_current_broker_authority

    async def approve_job(self, command: ApproveWorkflowJobCommand) -> ApprovalGrant:
        async with self._database.transaction() as session:
            locator = (
                await session.execute(
                    sa.select(WorkflowJobRow.workflow_id).where(WorkflowJobRow.id == command.job_id)
                )
            ).scalar_one_or_none()
            if locator is None:
                raise WorkflowLifecycleError("Send Job does not exist")
            workflow = await session.scalar(
                sa.select(WorkflowRow).where(WorkflowRow.id == locator).with_for_update()
            )
            job = await session.scalar(
                sa.select(WorkflowJobRow)
                .where(
                    WorkflowJobRow.workflow_id == locator,
                    WorkflowJobRow.id == command.job_id,
                )
                .with_for_update()
            )
            if workflow is None or job is None:
                raise WorkflowLifecycleError("Send Job aggregate does not exist")

            existing = await session.scalar(
                sa.select(WorkflowEventRow).where(
                    WorkflowEventRow.workflow_id == workflow.id,
                    WorkflowEventRow.event_type == "approval_granted",
                    WorkflowEventRow.cause_type == command.context.cause_type,
                    WorkflowEventRow.cause_id == command.context.cause_id,
                )
            )
            if existing is not None:
                grant = self._grant(existing)
                if (
                    grant.job_id != command.job_id
                    or grant.draft_job_id != command.expected_draft_revision_id
                ):
                    raise WorkflowLifecycleError("Approval Cause was already used")
                return grant
            if workflow.status != "active" or job.kind != GMAIL_SEND_EMAIL_KIND:
                raise WorkflowLifecycleError("Job is not approvable")
            if job.status != "waiting":
                raise WorkflowLifecycleError("Approval command is stale")
            if not await self._has_current_broker_authority(
                session,
                command.context,
                workflow,
            ):
                raise WorkflowAuthorizationError("Party cannot approve this Send Job")

            presentation = await session.scalar(
                sa.select(WorkflowEventRow)
                .where(
                    WorkflowEventRow.workflow_id == workflow.id,
                    WorkflowEventRow.job_id == job.id,
                    WorkflowEventRow.event_type == "approval_presentation_committed",
                )
                .order_by(WorkflowEventRow.occurred_at.desc(), WorkflowEventRow.id.desc())
                .limit(1)
            )
            if presentation is None:
                raise WorkflowLifecycleError("Exact effect was not presented for approval")
            if str(command.expected_draft_revision_id) != presentation.data.get("draft_job_id"):
                raise WorkflowLifecycleError("Presented Draft Revision is stale")

            effect = await resolve_email_effect(session, workflow.id, job)
            fingerprint = fingerprint_email_effect(effect)
            if fingerprint != presentation.data.get("effect_fingerprint"):
                raise WorkflowLifecycleError("Presented effect fingerprint does not match")

            grant = WorkflowEventRow(
                workflow_id=workflow.id,
                job_id=job.id,
                event_type="approval_granted",
                actor_type="party",
                actor_id=str(command.context.actor_party_id),
                cause_type=command.context.cause_type,
                cause_id=command.context.cause_id,
                data={
                    "draft_revision_id": str(command.expected_draft_revision_id),
                    "effect_fingerprint": fingerprint,
                },
            )
            session.add(grant)
            job.status = "queued"
            await session.flush()
            return self._grant(grant)

    @staticmethod
    def _grant(event: WorkflowEventRow) -> ApprovalGrant:
        if event.job_id is None or event.cause_type not in {"message", "ui_action"}:
            raise WorkflowLifecycleError("Approval Grant identity is invalid")
        try:
            draft_job_id = event.data["draft_revision_id"]
            fingerprint = event.data["effect_fingerprint"]
        except KeyError as exc:
            raise WorkflowLifecycleError("Approval Grant evidence is invalid") from exc
        return ApprovalGrant(
            approval_grant_id=event.id,
            workflow_id=event.workflow_id,
            job_id=event.job_id,
            approving_party_id=event.actor_id,
            draft_job_id=draft_job_id,
            effect_fingerprint=fingerprint,
            cause_type=cast(Literal["message", "ui_action"], event.cause_type),
            cause_id=event.cause_id,
            granted_at=event.occurred_at,
        )


__all__ = ["WorkflowApprovalProtocol"]
