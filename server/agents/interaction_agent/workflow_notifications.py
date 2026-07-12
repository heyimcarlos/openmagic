"""Fresh, identifier-driven Interaction Agent Notification handling."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa

from server.services.conversation import get_conversation_log
from server.workflows import WorkflowInspectionContext, WorkflowRetrieval
from server.workflows.database import WorkflowDatabase
from server.workflows.errors import NotificationLifecycleError
from server.workflows.models import NotificationRow, WorkflowEventRow
from server.workflows.registry import GMAIL_SEND_EMAIL_KIND


class ApprovalPresenter(Protocol):
    """Commit one exact approval request to the user-facing message boundary."""

    async def present(self, effect: dict[str, object]) -> None: ...


class ConversationApprovalPresenter:
    """Render every effect-defining field without model paraphrasing."""

    async def present(self, effect: dict[str, object]) -> None:
        get_conversation_log().record_reply(
            FreshWorkflowInteraction.render_approval_request(effect)
        )


class FreshWorkflowInteraction:
    """Handle one Workflow Notification without loading conversation history."""

    def __init__(
        self,
        *,
        database: WorkflowDatabase,
        retrieval: WorkflowRetrieval,
        presenter: ApprovalPresenter,
    ) -> None:
        self.runtime_instance_id = uuid4()
        self._database = database
        self._retrieval = retrieval
        self._presenter = presenter
        self._used = False

    async def handle(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
    ) -> None:
        if self._used:
            raise RuntimeError("A Notification Interaction runtime may handle only once")
        self._used = True
        actor_party_id = await self._resolve_destination(
            notification_id,
            workflow_event_id,
            workflow_id,
        )
        packet = await self._retrieval.read_workflow_packet(
            WorkflowInspectionContext(actor_party_id=actor_party_id),
            workflow_id,
        )
        send_jobs = [job for job in packet.jobs if job.kind == GMAIL_SEND_EMAIL_KIND]
        if len(send_jobs) != 1 or send_jobs[0].resolved_input is None:
            raise NotificationLifecycleError("Approval Notification has no resolved Send input")
        await self._presenter.present(send_jobs[0].resolved_input)

    async def _resolve_destination(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
    ) -> UUID:
        async with self._database.read_transaction() as session:
            row = (
                await session.execute(
                    sa.select(NotificationRow, WorkflowEventRow)
                    .join(
                        WorkflowEventRow,
                        sa.and_(
                            WorkflowEventRow.workflow_id == NotificationRow.workflow_id,
                            WorkflowEventRow.id == NotificationRow.workflow_event_id,
                        ),
                    )
                    .where(
                        NotificationRow.id == notification_id,
                        NotificationRow.workflow_event_id == workflow_event_id,
                        NotificationRow.workflow_id == workflow_id,
                    )
                )
            ).one_or_none()
        if row is None:
            raise NotificationLifecycleError("Notification identifiers do not match")
        notification, event = row
        if (
            notification.status != "delivering"
            or notification.kind != "approval_required"
            or notification.destination_type != "party"
            or event.event_type != "draft_ready"
        ):
            raise NotificationLifecycleError("Notification is not deliverable for approval")
        try:
            return UUID(notification.destination_id)
        except ValueError as exc:
            raise NotificationLifecycleError("Notification destination is invalid") from exc

    @staticmethod
    def render_approval_request(effect: dict[str, object]) -> str:
        def addresses(field: str) -> str:
            values = effect.get(field)
            if not isinstance(values, list | tuple):
                raise NotificationLifecycleError(f"Resolved Send input lacks {field}")
            return ", ".join(str(value) for value in values) or "None"

        sender = effect.get("sender_mailbox")
        subject = effect.get("subject")
        body = effect.get("body")
        if not all(isinstance(value, str) and value for value in (sender, subject, body)):
            raise NotificationLifecycleError("Resolved Send input is incomplete")
        return (
            "This exact renewal email is ready for your approval:\n\n"
            f"From: {sender}\n"
            f"To: {addresses('to')}\n"
            f"Cc: {addresses('cc')}\n"
            f"Bcc: {addresses('bcc')}\n"
            f"Subject: {subject}\n\n"
            f"{body}\n\n"
            "Reply with an explicit approval to send this exact email, or request changes."
        )


class FreshWorkflowInteractionFactory:
    """Create one history-free Interaction runtime per delivery attempt."""

    def __init__(
        self,
        *,
        database: WorkflowDatabase,
        retrieval: WorkflowRetrieval,
        presenter: ApprovalPresenter | None = None,
    ) -> None:
        self._database = database
        self._retrieval = retrieval
        self._presenter = presenter or ConversationApprovalPresenter()

    @asynccontextmanager
    async def create(self):
        runtime = FreshWorkflowInteraction(
            database=self._database,
            retrieval=self._retrieval,
            presenter=self._presenter,
        )
        try:
            yield runtime
        finally:
            del runtime
