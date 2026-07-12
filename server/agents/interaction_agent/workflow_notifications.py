"""Fresh, identifier-driven Interaction Agent Notification handling."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from server.config import Settings
from server.services.conversation import get_conversation_log
from server.workflows import (
    NotificationLifecycleError,
    WorkflowControlPlane,
    WorkflowInspectionContext,
    WorkflowRetrieval,
)

from .runtime import InteractionAgentRuntime
from .toolbox import InteractionToolContext, ToolResult


class ApprovalPresenter(Protocol):
    """Commit one exact approval request to the user-facing message boundary."""

    async def present(
        self,
        notification_id: UUID,
        destination_party_id: UUID,
        effect: dict[str, object],
    ) -> str: ...


class ConversationApprovalPresenter:
    """Render every effect-defining field without model paraphrasing."""

    def __init__(self, expected_party_id: UUID) -> None:
        self._expected_party_id = expected_party_id

    async def present(
        self,
        notification_id: UUID,
        destination_party_id: UUID,
        effect: dict[str, object],
    ) -> str:
        if destination_party_id != self._expected_party_id:
            raise NotificationLifecycleError("Notification targets a different Party")
        message = FreshWorkflowInteraction.render_approval_request(effect)
        get_conversation_log().record_reply_once(
            str(notification_id),
            message,
        )
        return message


class _NotificationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ReadPacketArguments(_NotificationArguments):
    workflow_id: UUID


class _PresentArguments(_NotificationArguments):
    pass


_NOTIFICATION_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "read_workflow_packet",
            "description": "Read the fresh operational packet for this Notification's Workflow.",
            "parameters": _ReadPacketArguments.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "present_approval_request",
            "description": "Present the exact Control Plane-selected Send Job for approval.",
            "parameters": _PresentArguments.model_json_schema(),
        },
    },
)

_NOTIFICATION_PROMPT = """You handle one Workflow Notification in a fresh context.
Read the supplied Workflow Packet, verify that approval is required, then call
present_approval_request. Do not approve, edit, summarize, select a Job, or
paraphrase the email. You have no previous conversation context."""


class _NotificationToolbox:
    def __init__(
        self,
        *,
        retrieval: WorkflowRetrieval,
        control_plane: WorkflowControlPlane,
        presenter: ApprovalPresenter,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
        destination_party_id: UUID,
        worker_id: str,
        delivery_attempt: int,
    ) -> None:
        self._retrieval = retrieval
        self._control_plane = control_plane
        self._presenter = presenter
        self._notification_id = notification_id
        self._workflow_event_id = workflow_event_id
        self._workflow_id = workflow_id
        self._destination_party_id = destination_party_id
        self._worker_id = worker_id
        self._delivery_attempt = delivery_attempt

    @property
    def schemas(self) -> tuple[dict[str, Any], ...]:
        return _NOTIFICATION_TOOLS

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        context: InteractionToolContext,
    ) -> ToolResult:
        if name == "read_workflow_packet":
            request = _ReadPacketArguments.model_validate(arguments)
            if request.workflow_id != context.trusted_workflow_id:
                return ToolResult(success=False, payload={"code": "wrong_workflow"})
            packet = await self._retrieval.read_workflow_packet(
                WorkflowInspectionContext(actor_party_id=context.actor_party_id),
                request.workflow_id,
            )
            context.loaded_packet = packet
            return ToolResult(success=True, payload=packet.model_dump(mode="json"))
        if name == "present_approval_request":
            _PresentArguments.model_validate(arguments)
            packet = context.loaded_packet
            if packet is None:
                return ToolResult(success=False, payload={"code": "workflow_packet_required"})
            presentation = await self._control_plane.resolve_notification_presentation(
                self._notification_id,
                self._workflow_event_id,
                self._workflow_id,
                self._worker_id,
                self._delivery_attempt,
            )
            if presentation.destination_party_id != self._destination_party_id:
                return ToolResult(success=False, payload={"code": "destination_changed"})
            message = await self._presenter.present(
                self._notification_id,
                self._destination_party_id,
                presentation.effect,
            )
            return ToolResult(
                success=True,
                payload={"status": "presented"},
                user_message=message,
                recorded_reply=True,
            )
        return ToolResult(success=False, payload={"code": "unknown_tool"})


def _build_notification_prompt() -> str:
    return _NOTIFICATION_PROMPT


def _prepare_notification_message(
    latest_text: str,
    _transcript: str,
    message_type: str = "agent",
) -> list[dict[str, str]]:
    return [{"role": "user", "content": f"<{message_type}>{latest_text}</{message_type}>"}]


class FreshWorkflowInteraction:
    """Handle one Workflow Notification without loading conversation history."""

    def __init__(
        self,
        *,
        control_plane: WorkflowControlPlane,
        retrieval: WorkflowRetrieval,
        presenter: ApprovalPresenter,
        worker_id: str,
        delivery_attempt: int,
        settings: Settings,
        organization_party_id: UUID,
    ) -> None:
        self.runtime_instance_id = uuid4()
        self._control_plane = control_plane
        self._retrieval = retrieval
        self._presenter = presenter
        self._worker_id = worker_id
        self._delivery_attempt = delivery_attempt
        self._settings = settings
        self._organization_party_id = organization_party_id
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
            self._worker_id,
            self._delivery_attempt,
        )
        toolbox = _NotificationToolbox(
            retrieval=self._retrieval,
            control_plane=self._control_plane,
            presenter=self._presenter,
            notification_id=notification_id,
            workflow_event_id=workflow_event_id,
            workflow_id=workflow_id,
            destination_party_id=presentation.destination_party_id,
            worker_id=self._worker_id,
            delivery_attempt=self._delivery_attempt,
        )
        runtime = InteractionAgentRuntime(
            toolbox=toolbox,
            system_prompt_builder=_build_notification_prompt,
            message_builder=_prepare_notification_message,
            settings=self._settings,
        )
        result = await runtime.execute_fresh_notification(
            json.dumps(
                {
                    "notification_id": str(notification_id),
                    "workflow_event_id": str(workflow_event_id),
                    "workflow_id": str(workflow_id),
                },
                sort_keys=True,
            ),
            InteractionToolContext(
                actor_party_id=presentation.destination_party_id,
                organization_party_id=self._organization_party_id,
                cause_id=f"notification:{notification_id}",
                trusted_workflow_id=workflow_id,
            ),
        )
        if not result.success:
            raise NotificationLifecycleError("Notification Interaction Agent failed")

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
        settings: Settings,
        organization_party_id: UUID,
    ) -> None:
        self._control_plane = control_plane
        self._retrieval = retrieval
        self._presenter = presenter
        self._settings = settings
        self._organization_party_id = organization_party_id

    @asynccontextmanager
    async def create(self, worker_id: str, delivery_attempt: int):
        runtime = FreshWorkflowInteraction(
            control_plane=self._control_plane,
            retrieval=self._retrieval,
            presenter=self._presenter,
            worker_id=worker_id,
            delivery_attempt=delivery_attempt,
            settings=self._settings,
            organization_party_id=self._organization_party_id,
        )
        try:
            yield runtime
        finally:
            del runtime
