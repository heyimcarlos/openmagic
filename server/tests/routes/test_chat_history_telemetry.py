from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from server.models import ChatApprovalCommand, ChatTurnTelemetry
from server.routes import chat as chat_route
from server.services.conversation.log import ConversationLog
from server.workflows import ResolvedSmsParty


class _WorkingMemory:
    def append_entry(self, tag: str, payload: str, timestamp: str) -> None:
        del tag, payload, timestamp
        return None

    def clear(self) -> None:
        return None


def _conversation(path: Path) -> ConversationLog:
    return ConversationLog(path, working_memory_log=_WorkingMemory())


async def test_history_projects_each_cause_only_onto_its_assistant_reply(
    tmp_path: Path,
    monkeypatch,
):
    log = _conversation(tmp_path / "conversation.log")
    log.record_user_message("First request", cause_id="cause-1")
    log.record_user_message("Second request", cause_id="cause-2")
    log.record_reply("First reply", cause_id="cause-1")
    log.record_reply("Second reply", cause_id="cause-2")
    log.record_reply("Resumed first reply", cause_id="cause-1")
    projected_causes: list[str] = []

    class Projector:
        async def project(self, *, actor_party_id, cause_ids):
            assert actor_party_id == UUID("10000000-0000-0000-0000-000000000001")
            projected_causes.extend(cause_ids)
            return {
                "cause-1": ChatTurnTelemetry(
                    activity_summary="Completed 1 Agent action",
                    activity=[
                        {
                            "id": "receipt-1",
                            "label": "Searched authorized Workflows",
                            "status": "succeeded",
                        }
                    ],
                    workflows=[],
                )
            }

    monkeypatch.setattr(
        chat_route,
        "get_settings",
        lambda: SimpleNamespace(
            interaction_mode="workflow",
            database_url="postgresql+psycopg://unused",
            workflow_cursor_secret="test-secret",
        ),
    )
    monkeypatch.setattr(
        chat_route,
        "get_conversation_session",
        lambda _interaction_id: SimpleNamespace(log=log),
    )
    monkeypatch.setattr(
        chat_route,
        "_workflow_telemetry_services",
        lambda _url, _secret: (object(), Projector()),
    )

    async def find_party(_database, _phone):
        return ResolvedSmsParty(
            party_id=UUID("10000000-0000-0000-0000-000000000001"),
            display_name="Carlos Broker",
            phone="+14165550142",
        )

    monkeypatch.setattr(chat_route, "find_sms_party", find_party)

    response = await chat_route.chat_history(sender_phone="+14165550142")

    assert projected_causes == ["cause-1", "cause-2"]
    replies = [message for message in response.messages if message.role == "assistant"]
    assert replies[0].id == "reply:cause-1"
    assert replies[0].telemetry is None
    assert replies[1].id == "reply:cause-2"
    assert replies[1].telemetry is None
    assert replies[2].id == "reply:cause-1:2"
    assert replies[2].telemetry is not None


async def test_history_returns_text_when_telemetry_projection_fails(tmp_path: Path, monkeypatch):
    log = _conversation(tmp_path / "conversation.log")
    log.record_user_message("Start work", cause_id="cause-1")
    log.record_reply("Work started", cause_id="cause-1")

    class Projector:
        async def project(self, **_kwargs):
            raise RuntimeError("projection unavailable")

    monkeypatch.setattr(
        chat_route,
        "get_settings",
        lambda: SimpleNamespace(
            interaction_mode="workflow",
            database_url="postgresql+psycopg://unused",
            workflow_cursor_secret="test-secret",
        ),
    )
    monkeypatch.setattr(
        chat_route,
        "get_conversation_session",
        lambda _interaction_id: SimpleNamespace(log=log),
    )
    monkeypatch.setattr(
        chat_route,
        "_workflow_telemetry_services",
        lambda _url, _secret: (object(), Projector()),
    )

    async def find_party(_database, _phone):
        return ResolvedSmsParty(
            party_id=UUID("10000000-0000-0000-0000-000000000001"),
            display_name="Carlos Broker",
            phone="+14165550142",
        )

    monkeypatch.setattr(chat_route, "find_sms_party", find_party)

    response = await chat_route.chat_history(sender_phone="+14165550142")

    assert [message.content for message in response.messages] == ["Start work", "Work started"]
    assert all(message.telemetry is None for message in response.messages)


async def test_latest_telemetry_projects_only_bounded_recent_reply_causes(
    tmp_path: Path,
    monkeypatch,
):
    log = _conversation(tmp_path / "conversation.log")
    for index in range(25):
        cause_id = f"cause-{index}"
        log.record_user_message(f"Request {index}", cause_id=cause_id)
        log.record_reply(f"Reply {index}", cause_id=cause_id)
    projected_causes: list[str] = []
    latest = ChatTurnTelemetry(
        activity_summary="Updated 1 Workflow",
        workflows=[
            {
                "id": "workflow-1",
                "title": "John renewal outreach",
                "status_label": "Waiting for approval",
                "stages": [],
            }
        ],
    )

    class Projector:
        async def project(self, *, actor_party_id, cause_ids):
            assert actor_party_id == UUID("10000000-0000-0000-0000-000000000001")
            projected_causes.extend(cause_ids)
            return {"cause-23": latest, "cause-4": latest}

    monkeypatch.setattr(
        chat_route,
        "get_settings",
        lambda: SimpleNamespace(
            interaction_mode="workflow",
            database_url="postgresql+psycopg://unused",
            workflow_cursor_secret="test-secret",
        ),
    )
    monkeypatch.setattr(
        chat_route,
        "get_conversation_session",
        lambda _interaction_id: SimpleNamespace(log=log),
    )
    monkeypatch.setattr(
        chat_route,
        "_workflow_telemetry_services",
        lambda _url, _secret: (object(), Projector()),
    )

    async def find_party(_database, _phone):
        return ResolvedSmsParty(
            party_id=UUID("10000000-0000-0000-0000-000000000001"),
            display_name="Carlos Broker",
            phone="+14165550142",
        )

    monkeypatch.setattr(chat_route, "find_sms_party", find_party)

    response = await chat_route.latest_chat_telemetry(sender_phone="+14165550142")

    assert projected_causes == [f"cause-{index}" for index in range(24, 4, -1)]
    assert response.telemetry == latest


async def test_direct_approval_uses_ui_cause_and_current_verification_session(monkeypatch):
    actor_id = UUID("10000000-0000-0000-0000-000000000001")
    organization_id = UUID("20000000-0000-0000-0000-000000000001")
    workflow_id = UUID("40000000-0000-0000-0000-000000000001")
    job_id = UUID("50000000-0000-0000-0000-000000000001")
    draft_id = UUID("50000000-0000-0000-0000-000000000002")
    recorded = []
    approved = []

    class ControlPlane:
        async def record_interaction_cause(self, command):
            recorded.append(command)

        async def approve_job(self, command):
            approved.append(command)
            return SimpleNamespace(job_id=command.job_id)

    class Verification:
        async def authorize_or_challenge(self, command):
            assert command.actor_party_id == actor_id
            assert command.workflow_id == workflow_id
            assert command.cause_type == "ui_action"
            assert command.operation.name == "approve_job"
            return SimpleNamespace(status="session_valid")

    monkeypatch.setattr(
        chat_route,
        "get_settings",
        lambda: SimpleNamespace(
            interaction_mode="workflow",
            database_url="postgresql+psycopg://unused",
            workflow_organization_party_id=str(organization_id),
            verification_code_secret="verification-secret",
            composio_api_key="composio-key",
            workflow_composio_user_id="broker-user",
        ),
    )
    monkeypatch.setattr(
        chat_route,
        "_workflow_approval_services",
        lambda *_args: (object(), ControlPlane(), Verification()),
    )

    async def find_party(_database, _phone):
        return ResolvedSmsParty(
            party_id=actor_id,
            display_name="Carlos Broker",
            phone="+14165550101",
        )

    monkeypatch.setattr(chat_route, "find_sms_party", find_party)

    response = await chat_route.approve_exact_email(
        ChatApprovalCommand(
            sender_phone="+14165550101",
            cause_id="ui-approval-1",
            workflow_id=workflow_id,
            job_id=job_id,
            expected_draft_revision_id=draft_id,
        )
    )

    assert response.status == "approved"
    assert response.job_id == job_id
    assert recorded[0].context.cause_type == "ui_action"
    assert approved[0].context.cause_type == "ui_action"
    assert approved[0].expected_draft_revision_id == draft_id
