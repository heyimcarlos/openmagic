from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from server.models import ChatRequest
from server.services.conversation import chat_handler
from server.workflows import ProtectedOperation, ResolvedSmsParty, VerificationCodeResult


@pytest.mark.parametrize(
    "content",
    [
        "482913",
        "My verification code is 482913.",
        "My verification code is 482-913.",
        "My verification code is 482 913.",
    ],
)
async def test_six_digit_sms_reply_queues_durable_resume_without_model_or_secret_log(
    monkeypatch,
    content,
):
    party_id = UUID("30000000-0000-0000-0000-000000000001")
    challenge_id = uuid4()
    operation = ProtectedOperation(
        name="read_workflow_packet",
        arguments={"workflow_id": "40000000-0000-0000-0000-000000000001"},
    )
    model_calls: list[dict] = []

    class FakeDatabase:
        async def dispose(self) -> None:
            return None

    class FakeRuntime:
        async def execute(self, **kwargs) -> None:
            model_calls.append(kwargs)

    class FakeVerification:
        async def submit_code(self, command):
            assert command.actor_party_id == party_id
            assert command.code == "482913"
            return VerificationCodeResult(
                status="verified",
                challenge_id=challenge_id,
                workflow_id=UUID("40000000-0000-0000-0000-000000000001"),
                purpose="sensitive_read",
                request_cause_id="private-read-message",
                operation=operation,
            )

    recorded_user_messages: list[str] = []
    recorded_replies: list[tuple[str, str | None]] = []
    session = SimpleNamespace(
        log=SimpleNamespace(
            record_user_message=lambda message, **_kwargs: recorded_user_messages.append(message),
            record_reply=lambda message, **kwargs: recorded_replies.append(
                (message, kwargs.get("cause_id"))
            ),
        ),
        working_memory=SimpleNamespace(render_transcript=lambda: ""),
    )
    monkeypatch.setattr(
        chat_handler,
        "get_settings",
        lambda: SimpleNamespace(
            interaction_mode="workflow",
            database_url="postgresql+psycopg://unused",
        ),
    )
    monkeypatch.setattr(chat_handler, "WorkflowDatabase", lambda _url: FakeDatabase())
    monkeypatch.setattr(
        chat_handler,
        "resolve_sms_party",
        lambda _database, _phone: _resolved_party(party_id),
    )
    monkeypatch.setattr(chat_handler, "get_conversation_session", lambda _id: session)
    monkeypatch.setattr(
        chat_handler, "create_interaction_runtime", lambda *_args, **_kwargs: FakeRuntime()
    )
    monkeypatch.setattr(
        chat_handler, "get_step_up_verification", lambda _settings: FakeVerification()
    )
    request = ChatRequest.model_validate(
        {
            "messages": [{"id": "sms-code-message", "role": "user", "content": content}],
            "interaction": {
                "channel": "sms",
                "sender_phone": "+1 (416) 555-0142",
            },
        }
    )

    response = await chat_handler.handle_chat_request(request)
    await asyncio.sleep(0)

    assert response.status_code == 202
    assert model_calls == []
    assert recorded_user_messages == ["[Verification code submitted]"]
    assert recorded_replies == [
        ("Your identity is verified. I'm continuing your request.", "sms-code-message")
    ]
    assert "482913" not in repr(session)


async def test_demo_reset_barrier_cancels_in_flight_interaction_before_continuing(monkeypatch):
    party_id = UUID("30000000-0000-0000-0000-000000000001")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class FakeDatabase:
        async def dispose(self) -> None:
            return None

    class FakeRuntime:
        async def execute(self, **_kwargs) -> None:
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    session = SimpleNamespace(
        log=SimpleNamespace(),
        working_memory=SimpleNamespace(render_transcript=lambda: ""),
    )
    monkeypatch.setattr(
        chat_handler,
        "get_settings",
        lambda: SimpleNamespace(
            interaction_mode="workflow",
            database_url="postgresql+psycopg://unused",
        ),
    )
    monkeypatch.setattr(chat_handler, "WorkflowDatabase", lambda _url: FakeDatabase())
    monkeypatch.setattr(
        chat_handler,
        "resolve_sms_party",
        lambda _database, _phone: _resolved_party(party_id),
    )
    monkeypatch.setattr(chat_handler, "get_conversation_session", lambda _id: session)
    monkeypatch.setattr(
        chat_handler,
        "create_interaction_runtime",
        lambda *_args, **_kwargs: FakeRuntime(),
    )
    request = ChatRequest.model_validate(
        {
            "messages": [{"id": "in-flight", "role": "user", "content": "Start work"}],
            "interaction": {
                "channel": "sms",
                "sender_phone": "+1 (416) 555-0142",
            },
        }
    )

    response = await chat_handler.handle_chat_request(request)
    await started.wait()

    async with chat_handler.pause_chat_requests():
        assert cancelled.is_set()
        assert not chat_handler._BACKGROUND_TASKS

    assert response.status_code == 202


async def _resolved_party(party_id: UUID) -> ResolvedSmsParty:
    return ResolvedSmsParty(
        party_id=party_id,
        display_name="John Smith",
        phone="+14165550142",
    )
