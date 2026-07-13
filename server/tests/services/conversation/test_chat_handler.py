from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

from server.models import ChatRequest
from server.services.conversation import chat_handler
from server.workflows import ProtectedOperation, ResolvedSmsParty, VerificationCodeResult


async def test_six_digit_sms_reply_resumes_stored_operation_before_model_interpretation(
    monkeypatch,
):
    party_id = UUID("30000000-0000-0000-0000-000000000001")
    challenge_id = uuid4()
    operation = ProtectedOperation(
        name="read_workflow_packet",
        arguments={"workflow_id": "40000000-0000-0000-0000-000000000001"},
    )
    resumed: list[dict] = []

    class FakeDatabase:
        async def dispose(self) -> None:
            return None

    class FakeRuntime:
        async def execute_verified_resume(self, **kwargs) -> None:
            resumed.append(kwargs)

        async def execute(self, **kwargs) -> None:
            raise AssertionError(f"Verification code reached normal model path: {kwargs}")

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

    session = SimpleNamespace(
        log=SimpleNamespace(
            record_user_message=lambda _message: None, record_reply=lambda _message: None
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
            "messages": [{"id": "sms-code-message", "role": "user", "content": "482913"}],
            "interaction": {
                "channel": "sms",
                "sender_phone": "+1 (416) 555-0142",
            },
        }
    )

    response = await chat_handler.handle_chat_request(request)
    await asyncio.sleep(0)

    assert response.status_code == 202
    assert resumed == [
        {
            "user_message": "482913",
            "operation_cause_id": "private-read-message",
            "challenge_id": challenge_id,
            "workflow_id": UUID("40000000-0000-0000-0000-000000000001"),
            "operation": operation,
        }
    ]


async def _resolved_party(party_id: UUID) -> ResolvedSmsParty:
    return ResolvedSmsParty(
        party_id=party_id,
        display_name="John Smith",
        phone="+14165550142",
    )
