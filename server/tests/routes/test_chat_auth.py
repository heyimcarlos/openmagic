from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from server.config import Settings
from server.models import ChatMessage, ChatRequest
from server.routes.chat import _require_workflow_interaction


def test_workflow_chat_requires_the_trusted_proxy_token():
    missing = Settings(interaction_mode="workflow", workflow_interaction_token=None)
    with pytest.raises(HTTPException) as unavailable:
        _require_workflow_interaction(missing, None)
    assert unavailable.value.status_code == 503

    configured = Settings(
        interaction_mode="workflow",
        workflow_interaction_token="local-secret",
    )
    with pytest.raises(HTTPException) as unauthorized:
        _require_workflow_interaction(configured, "Bearer wrong-secret")
    assert unauthorized.value.status_code == 401

    _require_workflow_interaction(configured, "Bearer local-secret")


def test_inbound_message_keeps_its_stable_source_id():
    message = ChatMessage.model_validate(
        {
            "id": "message-source-123",
            "role": "user",
            "content": "Yes, send this exact email",
        }
    )

    assert message.id == "message-source-123"


def test_sms_envelope_accepts_phone_identity_and_rejects_authorization_role():
    request = ChatRequest.model_validate(
        {
            "messages": [{"id": "sms-message-1", "role": "user", "content": "Hello"}],
            "interaction": {
                "channel": "sms",
                "sender_phone": "+1 (416) 555-0142",
            },
        }
    )

    assert request.interaction is not None
    assert request.interaction.sender_phone == "+1 (416) 555-0142"
    assert request.interaction.channel == "sms"

    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            {
                "messages": [{"id": "sms-message-2", "role": "user", "content": "Hello"}],
                "interaction": {
                    "channel": "sms",
                    "sender_phone": "+1 (416) 555-0142",
                    "authorization_role": "Broker",
                },
            }
        )
