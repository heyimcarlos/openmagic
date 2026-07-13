"""Safe immutable email revision and direct approval commands."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

import sqlalchemy as sa

from .authority import CurrentBrokerAuthority
from .contracts import (
    ApprovalGrant,
    ReviseAndApproveWorkflowEmailCommand,
    ReviseWorkflowEmailCommand,
    WorkflowEmailRevision,
)
from .database import WorkflowDatabase
from .email_effects import fingerprint_email_effect, resolve_email_effect
from .errors import WorkflowAuthorizationError, WorkflowLifecycleError
from .interaction_cause_protocol import WorkflowInteractionCauseProtocol
from .models import (
    NotificationRow,
    WorkflowEventRow,
    WorkflowJobDependencyRow,
    WorkflowJobRow,
    WorkflowJobRunRow,
    WorkflowRow,
)
from .registry import DRAFT_RENEWAL_EMAIL_KIND, GMAIL_SEND_EMAIL_KIND, WorkflowKindRegistry


class WorkflowEmailRevisionProtocol:
    """Replace one safely cancelable Send Job without mutating frozen work."""

    def __init__(
        self,
        database: WorkflowDatabase,
        registry: WorkflowKindRegistry,
        has_current_broker_authority: CurrentBrokerAuthority,
        causes: WorkflowInteractionCauseProtocol,
    ) -> None:
        self._database = database
        self._registry = registry
        self._has_current_broker_authority = has_current_broker_authority
        self._causes = causes

    async def revise_and_approve(
        self,
        command: ReviseAndApproveWorkflowEmailCommand,
    ) -> ApprovalGrant:
        result = await self._replace(command, approve=True)
        if not isinstance(result, ApprovalGrant):
            raise WorkflowLifecycleError("Revision approval receipt is incomplete")
        return result

    async def revise(self, command: ReviseWorkflowEmailCommand) -> WorkflowEmailRevision:
        result = await self._replace(command, approve=False)
        if not isinstance(result, WorkflowEmailRevision):
            raise WorkflowLifecycleError("Revision receipt is incomplete")
        return result

    async def _replace(
        self,
        command: ReviseWorkflowEmailCommand,
        *,
        approve: bool,
    ) -> ApprovalGrant | WorkflowEmailRevision:
        digest = self._command_digest(command, approve=approve)
        async with self._database.transaction() as session:
            workflow = await session.scalar(
                sa.select(WorkflowRow)
                .where(WorkflowRow.id == command.workflow_id)
                .with_for_update()
            )
            if workflow is None:
                raise WorkflowLifecycleError("Workflow does not exist")
            await self._causes.require(session, command.context)

            existing = await session.scalar(
                sa.select(WorkflowEventRow).where(
                    WorkflowEventRow.event_type == "workflow_work_revised",
                    WorkflowEventRow.cause_type == command.context.cause_type,
                    WorkflowEventRow.cause_id == command.context.cause_id,
                )
            )
            if existing is not None:
                return await self._replay(session, existing, command, digest, approve=approve)

            if workflow.status != "active":
                raise WorkflowLifecycleError("Workflow is not active")
            if not await self._has_current_broker_authority(session, command.context, workflow):
                raise WorkflowAuthorizationError("Party cannot revise this Workflow")

            send = await session.scalar(
                sa.select(WorkflowJobRow)
                .where(
                    WorkflowJobRow.workflow_id == workflow.id,
                    WorkflowJobRow.id == command.job_id,
                )
                .with_for_update()
            )
            if send is None or send.kind != GMAIL_SEND_EMAIL_KIND:
                raise WorkflowLifecycleError("Send Job does not exist")
            if send.status not in {"waiting", "queued", "running"}:
                raise WorkflowLifecycleError("Send Job is not safely replaceable")
            dispatched = await session.scalar(
                sa.select(WorkflowEventRow.id).where(
                    WorkflowEventRow.workflow_id == workflow.id,
                    WorkflowEventRow.job_id == send.id,
                    WorkflowEventRow.event_type == "external_effect_dispatch_started",
                )
            )
            if dispatched is not None:
                raise WorkflowLifecycleError("External Effect already dispatched")
            replacement = await session.scalar(
                sa.select(WorkflowJobRow.id).where(
                    WorkflowJobRow.workflow_id == workflow.id,
                    WorkflowJobRow.revises_job_id == send.id,
                )
            )
            if replacement is not None:
                raise WorkflowLifecycleError("Send Job was already replaced")

            dependency = await session.scalar(
                sa.select(WorkflowJobDependencyRow).where(
                    WorkflowJobDependencyRow.workflow_id == workflow.id,
                    WorkflowJobDependencyRow.job_id == send.id,
                    WorkflowJobDependencyRow.depends_on_job_id
                    == command.expected_draft_revision_id,
                )
            )
            draft = await session.scalar(
                sa.select(WorkflowJobRow)
                .where(
                    WorkflowJobRow.workflow_id == workflow.id,
                    WorkflowJobRow.id == command.expected_draft_revision_id,
                )
                .with_for_update()
            )
            if (
                dependency is None
                or draft is None
                or draft.kind != DRAFT_RENEWAL_EMAIL_KIND
                or draft.status != "succeeded"
                or draft.output is None
            ):
                raise WorkflowLifecycleError("Draft Revision is stale")

            await self._cancel_old_attempts(session, send)
            await self._invalidate_old_approval(session, send, command)
            send.status = "cancelled"

            revised_draft = WorkflowJobRow(
                id=uuid4(),
                workflow_id=workflow.id,
                kind=DRAFT_RENEWAL_EMAIL_KIND,
                status="succeeded",
                attempts=0,
                max_attempts=self._registry.job_contract(DRAFT_RENEWAL_EMAIL_KIND).max_attempts,
                input=draft.input,
                output={
                    "subject": command.email.subject,
                    "body": command.email.body,
                },
                revises_job_id=draft.id,
            )
            revised_send = WorkflowJobRow(
                id=uuid4(),
                workflow_id=workflow.id,
                kind=GMAIL_SEND_EMAIL_KIND,
                status="queued" if approve else "waiting",
                attempts=0,
                max_attempts=self._registry.job_contract(GMAIL_SEND_EMAIL_KIND).max_attempts,
                input={
                    "sender_mailbox": send.input["sender_mailbox"],
                    "to": [str(value) for value in command.email.to],
                    "cc": [str(value) for value in command.email.cc],
                    "bcc": [str(value) for value in command.email.bcc],
                    "subject": {
                        "job_output": str(revised_draft.id),
                        "field": "subject",
                    },
                    "body": {
                        "job_output": str(revised_draft.id),
                        "field": "body",
                    },
                },
                revises_job_id=send.id,
            )
            session.add_all((revised_draft, revised_send))
            await session.flush()
            session.add(
                WorkflowJobDependencyRow(
                    workflow_id=workflow.id,
                    job_id=revised_send.id,
                    depends_on_job_id=revised_draft.id,
                )
            )
            event_time = datetime.now(UTC)
            replacement_event = WorkflowEventRow(
                workflow_id=workflow.id,
                job_id=send.id,
                event_type="job_replaced",
                actor_type="party",
                actor_id=str(command.context.actor_party_id),
                cause_type=command.context.cause_type,
                cause_id=command.context.cause_id,
                data={"replacement_job_id": str(revised_send.id)},
                occurred_at=event_time,
            )
            draft_ready = WorkflowEventRow(
                workflow_id=workflow.id,
                job_id=revised_draft.id,
                event_type="draft_ready",
                actor_type="party",
                actor_id=str(command.context.actor_party_id),
                cause_type=command.context.cause_type,
                cause_id=command.context.cause_id,
                data={"revision_source": "inline_edit" if approve else "interaction_edit"},
                occurred_at=event_time + timedelta(microseconds=1),
            )
            revision = WorkflowEventRow(
                workflow_id=workflow.id,
                job_id=revised_send.id,
                event_type="workflow_work_revised",
                actor_type="party",
                actor_id=str(command.context.actor_party_id),
                cause_type=command.context.cause_type,
                cause_id=command.context.cause_id,
                data={
                    "command_digest": digest,
                    "draft_job_id": str(revised_draft.id),
                    "send_job_id": str(revised_send.id),
                    "replaces_draft_job_id": str(draft.id),
                    "replaces_send_job_id": str(send.id),
                    "approved": approve,
                },
                occurred_at=event_time + timedelta(microseconds=2),
            )
            session.add_all((replacement_event, draft_ready, revision))
            await session.flush()

            if not approve:
                session.add(
                    NotificationRow(
                        workflow_id=workflow.id,
                        workflow_event_id=draft_ready.id,
                        kind="approval_required",
                        destination_type="party",
                        destination_id=str(command.context.actor_party_id),
                        status="queued",
                        attempts=0,
                        max_attempts=3,
                    )
                )
                await session.flush()
                return WorkflowEmailRevision(
                    workflow_id=workflow.id,
                    draft_job_id=revised_draft.id,
                    send_job_id=revised_send.id,
                )

            effect = await resolve_email_effect(session, workflow.id, revised_send)
            fingerprint = fingerprint_email_effect(effect)
            session.add(
                WorkflowEventRow(
                    workflow_id=workflow.id,
                    job_id=revised_send.id,
                    event_type="approval_presentation_committed",
                    actor_type="party",
                    actor_id=str(command.context.actor_party_id),
                    cause_type=command.context.cause_type,
                    cause_id=command.context.cause_id,
                    data={
                        "draft_job_id": str(revised_draft.id),
                        "effect_fingerprint": fingerprint,
                        "sender_mailbox_id": str(effect.sender_mailbox_id),
                    },
                    occurred_at=event_time + timedelta(microseconds=3),
                )
            )
            grant = WorkflowEventRow(
                workflow_id=workflow.id,
                job_id=revised_send.id,
                event_type="approval_granted",
                actor_type="party",
                actor_id=str(command.context.actor_party_id),
                cause_type=command.context.cause_type,
                cause_id=command.context.cause_id,
                data={
                    "draft_revision_id": str(revised_draft.id),
                    "effect_fingerprint": fingerprint,
                },
                occurred_at=event_time + timedelta(microseconds=4),
            )
            session.add(grant)
            await session.flush()
            return self._grant(grant)

    async def _replay(
        self,
        session,
        event: WorkflowEventRow,
        command: ReviseWorkflowEmailCommand,
        digest: str,
        *,
        approve: bool,
    ) -> ApprovalGrant | WorkflowEmailRevision:
        if (
            event.workflow_id != command.workflow_id
            or event.actor_id != str(command.context.actor_party_id)
            or event.data.get("command_digest") != digest
        ):
            raise WorkflowLifecycleError("Revision Cause was already used")
        if not approve:
            try:
                return WorkflowEmailRevision(
                    workflow_id=event.workflow_id,
                    draft_job_id=UUID(str(event.data["draft_job_id"])),
                    send_job_id=UUID(str(event.data["send_job_id"])),
                )
            except (KeyError, ValueError) as exc:
                raise WorkflowLifecycleError("Revision receipt is incomplete") from exc
        grant = await session.scalar(
            sa.select(WorkflowEventRow).where(
                WorkflowEventRow.event_type == "approval_granted",
                WorkflowEventRow.cause_type == command.context.cause_type,
                WorkflowEventRow.cause_id == command.context.cause_id,
            )
        )
        if grant is None:
            raise WorkflowLifecycleError("Revision approval receipt is incomplete")
        return self._grant(grant)

    @staticmethod
    async def _cancel_old_attempts(session, send: WorkflowJobRow) -> None:
        runs = (
            await session.scalars(
                sa.select(WorkflowJobRunRow)
                .where(
                    WorkflowJobRunRow.workflow_id == send.workflow_id,
                    WorkflowJobRunRow.job_id == send.id,
                    WorkflowJobRunRow.status == "running",
                )
                .with_for_update()
            )
        ).all()
        now = datetime.now(UTC)
        for run in runs:
            run.status = "cancelled"
            run.finished_at = now

    @staticmethod
    async def _invalidate_old_approval(
        session,
        send: WorkflowJobRow,
        command: ReviseWorkflowEmailCommand,
    ) -> None:
        approvals = (
            await session.scalars(
                sa.select(WorkflowEventRow).where(
                    WorkflowEventRow.workflow_id == send.workflow_id,
                    WorkflowEventRow.job_id == send.id,
                    WorkflowEventRow.event_type == "approval_granted",
                )
            )
        ).all()
        for approval in approvals:
            invalidated = await session.scalar(
                sa.select(WorkflowEventRow.id).where(
                    WorkflowEventRow.workflow_id == send.workflow_id,
                    WorkflowEventRow.approval_grant_id == approval.id,
                    WorkflowEventRow.event_type == "approval_invalidated",
                )
            )
            if invalidated is not None:
                continue
            session.add(
                WorkflowEventRow(
                    workflow_id=send.workflow_id,
                    job_id=send.id,
                    approval_grant_id=approval.id,
                    event_type="approval_invalidated",
                    actor_type="party",
                    actor_id=str(command.context.actor_party_id),
                    cause_type=command.context.cause_type,
                    cause_id=command.context.cause_id,
                    data={
                        "reason": "job_replaced",
                        "approval_grant_id": str(approval.id),
                    },
                )
            )

    @staticmethod
    def _command_digest(command: ReviseWorkflowEmailCommand, *, approve: bool) -> str:
        payload = {
            "workflow_id": str(command.workflow_id),
            "job_id": str(command.job_id),
            "expected_draft_revision_id": str(command.expected_draft_revision_id),
            "email": command.email.model_dump(mode="json"),
            "approve": approve,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _grant(event: WorkflowEventRow) -> ApprovalGrant:
        if event.job_id is None or event.cause_type not in {"message", "ui_action"}:
            raise WorkflowLifecycleError("Approval Grant identity is invalid")
        return ApprovalGrant(
            approval_grant_id=event.id,
            workflow_id=event.workflow_id,
            job_id=event.job_id,
            approving_party_id=UUID(event.actor_id),
            draft_job_id=UUID(str(event.data["draft_revision_id"])),
            effect_fingerprint=str(event.data["effect_fingerprint"]),
            cause_type=cast(Literal["message", "ui_action"], event.cause_type),
            cause_id=event.cause_id,
            granted_at=event.occurred_at,
        )


__all__ = ["WorkflowEmailRevisionProtocol"]
