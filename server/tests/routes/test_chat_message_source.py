from __future__ import annotations

from server.models import ChatMessage


def test_inbound_message_keeps_its_stable_source_id():
    message = ChatMessage.model_validate(
        {
            "id": "message-source-123",
            "role": "user",
            "content": "Yes, send this exact email",
        }
    )

    assert message.id == "message-source-123"
