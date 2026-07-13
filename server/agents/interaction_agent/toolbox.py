"""Async tool boundary shared by legacy and Workflow interaction profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from server.workflows import WorkflowPacket


class ConversationRecorder(Protocol):
    def record_reply(self, message: str, *, cause_id: str | None = None) -> None: ...

    def record_reply_once(
        self,
        delivery_id: str,
        message: str,
        *,
        cause_id: str | None = None,
    ) -> bool: ...

    def record_wait(self, reason: str) -> None: ...


@dataclass
class InteractionToolContext:
    """Trusted per-turn context that is never model-provided."""

    actor_party_id: UUID
    organization_party_id: UUID
    cause_id: str
    cause_type: Literal["message", "ui_action"] = "message"
    interaction_id: str | None = None
    verification_challenge_id: UUID | None = None
    delivery_id: str | None = None
    conversation: ConversationRecorder | None = None
    trusted_workflow_id: UUID | None = None
    resolved_workflow_id: UUID | None = None
    loaded_packet: WorkflowPacket | None = None


@dataclass
class ToolResult:
    """Standardized payload returned by interaction-agent tools."""

    success: bool
    payload: Any = None
    user_message: str | None = None
    recorded_reply: bool = False


class InteractionToolbox(Protocol):
    """One explicit model-visible tool profile and its trusted implementation."""

    @property
    def schemas(self) -> tuple[dict[str, Any], ...]: ...

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        context: InteractionToolContext,
    ) -> ToolResult: ...
