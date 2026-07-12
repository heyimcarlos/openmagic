"""Durable dispatch boundary for approved irreversible External Effects."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import BeginExternalEffectDispatchCommand, WorkflowCommandContext
from .database import WorkflowDatabase
from .email_effects import (
    EmailSendDispatchV1,
    EmailSendExecutionContextV1,
    fingerprint_email_effect,
    resolve_email_effect,
)
from .errors import StaleRunError, WorkflowAuthorizationError, WorkflowLifecycleError
from .models import WorkflowEventRow, WorkflowJobRow, WorkflowJobRunRow, WorkflowRow
from .registry import GMAIL_SEND_EMAIL_KIND, WorkflowKindRegistry

CurrentBrokerAuthority = Callable[
    [AsyncSession, WorkflowCommandContext, WorkflowRow], Awaitable[bool]
]


class WorkflowExternalEffectProtocol:
    """Validate and commit one dispatch before any provider interaction."""

    def __init__(
        self,
        database: WorkflowDatabase,
        registry: WorkflowKindRegistry,
        has_current_broker_authority: CurrentBrokerAuthority,
    ) -> None:
        self._database = database
        self._registry = registry
        self._has_current_broker_authority = has_current_broker_authority

    async def begin_dispatch(
        self,
        command: BeginExternalEffectDispatchCommand,
    ) -> EmailSendDispatchV1:
        integrity_failed = False
        async with self._database.transaction() as session:
            locator = (
                await session.execute(
                    sa.select(
                        WorkflowJobRunRow.workflow_id,
                        WorkflowJobRunRow.job_id,
                    ).where(WorkflowJobRunRow.id == command.run_id)
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
                .where(
                    WorkflowJobRow.workflow_id == workflow_id,
                    WorkflowJobRow.id == job_id,
                )
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
                raise StaleRunError("Run aggregate does not exist")
            if (
                workflow.status != "active"
                or job.kind != GMAIL_SEND_EMAIL_KIND
                or job.status != "running"
                or run.status != "running"
                or run.lease_expires_at < datetime.now(UTC)
            ):
                raise StaleRunError("Run no longer has dispatch authority")
            prior_dispatch = await session.scalar(
                sa.select(WorkflowEventRow.id).where(
                    WorkflowEventRow.workflow_id == workflow.id,
                    WorkflowEventRow.job_id == job.id,
                    WorkflowEventRow.event_type == "external_effect_dispatch_started",
                )
            )
            if prior_dispatch is not None:
                raise WorkflowLifecycleError("External Effect dispatch already started")

            approval = await session.scalar(
                sa.select(WorkflowEventRow)
                .where(
                    WorkflowEventRow.workflow_id == workflow.id,
                    WorkflowEventRow.job_id == job.id,
                    WorkflowEventRow.event_type == "approval_granted",
                )
                .order_by(WorkflowEventRow.occurred_at.desc(), WorkflowEventRow.id.desc())
                .limit(1)
            )
            if approval is None:
                raise WorkflowLifecycleError("Send Job has no Approval Grant")
            invalidation = await session.scalar(
                sa.select(WorkflowEventRow.id).where(
                    WorkflowEventRow.workflow_id == workflow.id,
                    WorkflowEventRow.approval_grant_id == approval.id,
                    WorkflowEventRow.event_type == "approval_invalidated",
                )
            )
            if invalidation is not None:
                raise WorkflowLifecycleError("Approval Grant is invalidated")
            try:
                approving_party_id = UUID(approval.actor_id)
            except ValueError as exc:
                raise WorkflowLifecycleError("Approval Grant Party is invalid") from exc
            authority_context = WorkflowCommandContext(
                actor_party_id=approving_party_id,
                organization_party_id=workflow.organization_party_id,
                cause_type="message",
                cause_id=f"dispatch:{run.id}",
            )
            if not await self._has_current_broker_authority(
                session,
                authority_context,
                workflow,
            ):
                raise WorkflowAuthorizationError("Approving Party no longer has authority")

            effect = await resolve_email_effect(session, workflow.id, job)
            fingerprint = fingerprint_email_effect(effect)
            if fingerprint != approval.data.get("effect_fingerprint"):
                run.status = "cancelled"
                run.finished_at = datetime.now(UTC)
                job.status = "waiting"
                session.add(
                    WorkflowEventRow(
                        workflow_id=workflow.id,
                        job_id=job.id,
                        run_id=run.id,
                        event_type="effect_integrity_failed",
                        actor_type="system",
                        actor_id="workflow_control_plane",
                        cause_type="job",
                        cause_id=str(job.id),
                        data={"approval_grant_id": str(approval.id)},
                    )
                )
                await session.flush()
                integrity_failed = True
            else:
                contract = self._registry.job_contract(job.kind)
                run.adapter_version = contract.adapter_version
                run.provider_tool_version = contract.provider_tool_version
                session.add(
                    WorkflowEventRow(
                        workflow_id=workflow.id,
                        job_id=job.id,
                        run_id=run.id,
                        approval_grant_id=approval.id,
                        event_type="external_effect_dispatch_started",
                        actor_type="run",
                        actor_id=str(run.id),
                        cause_type="job",
                        cause_id=str(job.id),
                        data={"effect_fingerprint": fingerprint},
                    )
                )
                await session.flush()
                return EmailSendDispatchV1(
                    workflow_id=workflow.id,
                    approval_grant_id=approval.id,
                    effect=effect,
                    context=EmailSendExecutionContextV1(
                        job_id=job.id,
                        run_id=run.id,
                        effect_fingerprint=fingerprint,
                    ),
                    effect_fingerprint=fingerprint,
                )
        if integrity_failed:
            raise WorkflowLifecycleError("Approved effect fingerprint does not match")
        raise WorkflowLifecycleError("External Effect dispatch could not be committed")


__all__ = ["WorkflowExternalEffectProtocol"]
