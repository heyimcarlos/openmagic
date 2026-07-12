"""Async tool boundary shared by legacy and Workflow interaction profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from server.workflows import WorkflowPacket


@dataclass
class InteractionToolContext:
    """Trusted per-turn context that is never model-provided."""

    actor_party_id: UUID
    organization_party_id: UUID
    cause_id: str
    loaded_packets: dict[UUID, WorkflowPacket] = field(default_factory=dict)


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
