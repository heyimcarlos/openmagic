"""Fresh, identifier-driven Interaction Agent Notification handling."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Protocol
from uuid import UUID, uuid4

from server.services.conversation import get_conversation_log
from server.workflows import (
    GMAIL_SEND_EMAIL_KIND,
    NotificationLifecycleError,
    WorkflowControlPlane,
    WorkflowInspectionContext,
    WorkflowRetrieval,
)


class ApprovalPresenter(Protocol):
    """Commit one exact approval request to the user-facing message boundary."""

    async def present(
        self,
        notification_id: UUID,
        destination_party_id: UUID,
        effect: dict[str, object],
    ) -> None: ...


class ConversationApprovalPresenter:
    """Render every effect-defining field without model paraphrasing."""

    def __init__(self, expected_party_id: UUID) -> None:
        self._expected_party_id = expected_party_id

    async def present(
        self,
        notification_id: UUID,
        destination_party_id: UUID,
        effect: dict[str, object],
    ) -> None:
        if destination_party_id != self._expected_party_id:
            raise NotificationLifecycleError("Notification targets a different Party")
        get_conversation_log().record_reply_once(
            str(notification_id),
            FreshWorkflowInteraction.render_approval_request(effect),
        )


class FreshWorkflowInteraction:
    """Handle one Workflow Notification without loading conversation history."""

    def __init__(
        self,
        *,
        control_plane: WorkflowControlPlane,
        retrieval: WorkflowRetrieval,
        presenter: ApprovalPresenter,
    ) -> None:
        self.runtime_instance_id = uuid4()
        self._control_plane = control_plane
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
        presentation = await self._control_plane.resolve_notification_presentation(
            notification_id,
            workflow_event_id,
            workflow_id,
        )
        packet = await self._retrieval.read_workflow_packet(
            WorkflowInspectionContext(actor_party_id=presentation.destination_party_id),
            workflow_id,
        )
        send_jobs = [job for job in packet.jobs if job.kind == GMAIL_SEND_EMAIL_KIND]
        if len(send_jobs) != 1 or send_jobs[0].resolved_input is None:
            raise NotificationLifecycleError("Approval Notification has no resolved Send input")
        await self._presenter.present(
            notification_id,
            presentation.destination_party_id,
            send_jobs[0].resolved_input,
        )

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
        control_plane: WorkflowControlPlane,
        retrieval: WorkflowRetrieval,
        presenter: ApprovalPresenter,
    ) -> None:
        self._control_plane = control_plane
        self._retrieval = retrieval
        self._presenter = presenter

    @asynccontextmanager
    async def create(self):
        runtime = FreshWorkflowInteraction(
            control_plane=self._control_plane,
            retrieval=self._retrieval,
            presenter=self._presenter,
        )
        try:
            yield runtime
        finally:
            del runtime
