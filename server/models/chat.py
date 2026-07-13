from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

AgentActivityStatus = Literal["succeeded", "running", "failed"]
WorkflowJobStatus = Literal["waiting", "queued", "running", "succeeded", "failed", "cancelled"]
WorkflowCheckpointStatus = Literal["waiting", "satisfied", "unavailable"]
WorkflowEventTone = Literal["progress", "success", "terminal"]


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


class ChatApprovalRequest(BaseModel):
    """One exact, currently approvable email projected for the browser UI."""

    model_config = ConfigDict(extra="ignore")

    workflow_id: str = Field(..., min_length=1, max_length=255)
    job_id: str = Field(..., min_length=1, max_length=255)
    draft_revision_id: str = Field(..., min_length=1, max_length=255)
    revision: int = Field(..., ge=1)
    sender: str = Field(..., min_length=1, max_length=320)
    to: list[str] = Field(min_length=1)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str = Field(..., min_length=1, max_length=998)
    body: str = Field(..., min_length=1)


class ChatCockpitWorkflow(BaseModel):
    id: str
    kind: str
    objective: str
    organization: str
    status: Literal["active", "completed", "cancelled"]


class ChatCockpitJob(BaseModel):
    id: str
    kind: str
    title: str
    detail: str
    status: WorkflowJobStatus
    depends_on: list[str] = Field(default_factory=list)


class ChatCockpitEvent(BaseModel):
    id: str
    occurred_at: str
    type: str
    aggregate: str
    detail: str
    tone: WorkflowEventTone


class ChatWorkflowCockpit(BaseModel):
    workflow: ChatCockpitWorkflow
    jobs: list[ChatCockpitJob] = Field(default_factory=list)
    events: list[ChatCockpitEvent] = Field(default_factory=list)
    has_earlier_events: bool = False


class ChatTurnTelemetry(BaseModel):
    """Sanitized operational facts attached to one assistant chat turn."""

    model_config = ConfigDict(extra="ignore")

    activity_summary: str = Field(..., min_length=1, max_length=255)
    activity: list[ChatAgentActivity] = Field(default_factory=list)
    workflows: list[ChatWorkflowTelemetry] = Field(default_factory=list)
    approval_request: ChatApprovalRequest | None = None
    cockpit: ChatWorkflowCockpit | None = None


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


class ChatApprovalCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sender_phone: str = Field(min_length=8, max_length=32)
    cause_id: str = Field(min_length=1, max_length=255)
    workflow_id: UUID
    job_id: UUID
    expected_draft_revision_id: UUID


class ChatApprovalResponse(BaseModel):
    status: Literal["approved", "verification_required"]
    job_id: UUID | None = None
    masked_destination: str | None = None


class ChatHistoryClearResponse(BaseModel):
    ok: bool = True
