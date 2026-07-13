from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentActivityStatus = Literal["succeeded", "running", "failed"]
WorkflowJobStatus = Literal["waiting", "queued", "running", "succeeded", "failed", "cancelled"]
WorkflowCheckpointStatus = Literal["waiting", "satisfied", "unavailable"]


class ChatAgentActivity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1, max_length=255)
    label: str = Field(..., min_length=1, max_length=255)
    status: AgentActivityStatus


class ChatWorkflowJobStage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1, max_length=255)
    kind: Literal["job"]
    label: str = Field(..., min_length=1, max_length=255)
    status: WorkflowJobStatus


class ChatWorkflowCheckpoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1, max_length=255)
    kind: Literal["checkpoint"]
    label: str = Field(..., min_length=1, max_length=255)
    status: WorkflowCheckpointStatus


ChatWorkflowStage: TypeAlias = Annotated[
    ChatWorkflowJobStage | ChatWorkflowCheckpoint,
    Field(discriminator="kind"),
]


class ChatWorkflowTelemetry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=255)
    status_label: str = Field(..., min_length=1, max_length=255)
    stages: list[ChatWorkflowStage] = Field(default_factory=list)


class ChatTurnTelemetry(BaseModel):
    """Sanitized operational facts attached to one assistant chat turn."""

    model_config = ConfigDict(extra="ignore")

    activity_summary: str = Field(..., min_length=1, max_length=255)
    activity: list[ChatAgentActivity] = Field(default_factory=list)
    workflows: list[ChatWorkflowTelemetry] = Field(default_factory=list)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = Field(default=None, min_length=1, max_length=255)
    role: str = Field(..., min_length=1)
    content: str = Field(...)
    timestamp: str | None = Field(default=None)
    telemetry: ChatTurnTelemetry | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_content(cls, data: Any) -> Any:
        if isinstance(data, dict) and "content" in data:
            data["content"] = "" if data["content"] is None else str(data["content"])
        return data

    def as_openrouter(self) -> dict[str, str]:
        return {"role": self.role.strip(), "content": self.content}


class SmsInteractionEnvelope(BaseModel):
    """Demo-only browser envelope for one simulated inbound SMS interaction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    channel: Literal["sms"]
    sender_phone: str = Field(min_length=8, max_length=32)


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    messages: list[ChatMessage] = Field(default_factory=list)
    model: str | None = None
    system: str | None = None
    stream: bool = True
    interaction: SmsInteractionEnvelope | None = None

    def openrouter_messages(self) -> list[dict[str, str]]:
        return [msg.as_openrouter() for msg in self.messages if msg.content.strip()]


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)


class ChatLatestTelemetryResponse(BaseModel):
    telemetry: ChatTurnTelemetry | None = None


class ChatHistoryClearResponse(BaseModel):
    ok: bool = True
