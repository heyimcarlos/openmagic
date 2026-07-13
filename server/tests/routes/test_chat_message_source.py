from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.models import ChatMessage, ChatRequest


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
