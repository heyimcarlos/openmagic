from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = Field(default=None, min_length=1, max_length=255)
    role: str = Field(..., min_length=1)
    content: str = Field(...)
    timestamp: str | None = Field(default=None)

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


class ChatHistoryClearResponse(BaseModel):
    ok: bool = True
