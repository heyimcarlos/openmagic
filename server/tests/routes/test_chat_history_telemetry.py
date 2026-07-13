from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from server.agents.interaction_agent.workflow_notifications import ConversationApprovalPresenter
from server.models import ChatApprovalCommand, ChatApprovalRequest, ChatTurnTelemetry
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


async def test_approval_notification_reaches_the_sms_history_session(
    tmp_path: Path,
    monkeypatch,
):
    actor_id = UUID("10000000-0000-0000-0000-000000000001")
    notification_id = UUID("30000000-0000-0000-0000-000000000001")
    log = _conversation(tmp_path / "conversation.log")
    await ConversationApprovalPresenter(actor_id, conversation=log).present(
        notification_id,
        actor_id,
        {
            "expected_sender_address": "broker@acme.example",
            "to": ["john@example.com"],
            "cc": [],
            "bcc": [],
            "subject": "2026 renewal",
            "body": "Hello John",
        },
    )
    approval = ChatApprovalRequest(
        workflow_id="workflow-1",
        job_id="send-job-1",
        draft_revision_id="draft-job-1",
        revision=1,
        sender="broker@acme.example",
        to=["john@example.com"],
        subject="2026 renewal",
        body="Hello John",
    )

    class Projector:
        async def project(self, *, actor_party_id, cause_ids):
            assert actor_party_id == actor_id
            assert cause_ids == [f"notification:{notification_id}"]
            return {
                f"notification:{notification_id}": ChatTurnTelemetry(
                    activity_summary="Email ready for review",
                    approval_request=approval,
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
            party_id=actor_id,
            display_name="Carlos Broker",
            phone="+14165550142",
        )

    monkeypatch.setattr(chat_route, "find_sms_party", find_party)

    response = await chat_route.chat_history(sender_phone="+14165550142")

    assert len(response.messages) == 1
    assert response.messages[0].telemetry is not None
    assert response.messages[0].telemetry.approval_request == approval


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
    revised_job_id = UUID("50000000-0000-0000-0000-000000000003")
    recorded = []
    invoked = []

    class Retrieval:
        async def read_workflow_packet(self, context, requested_workflow_id):
            assert context.actor_party_id == actor_id
            assert requested_workflow_id == workflow_id
            return SimpleNamespace(workflow=SimpleNamespace(workflow_id=workflow_id))

    class Toolbox:
        async def record_interaction_cause(self, context, content):
            recorded.append((context, content))

        async def invoke(self, name, arguments, context):
            invoked.append((name, arguments, context))
            return SimpleNamespace(
                success=True,
                payload={
                    "status": "queued",
                    "job_id": str(revised_job_id if name == "revise_and_approve_email" else job_id),
                },
            )

    monkeypatch.setattr(
        chat_route,
        "get_settings",
        lambda: SimpleNamespace(
            interaction_mode="workflow",
            database_url="postgresql+psycopg://unused",
            workflow_cursor_secret="cursor-secret",
            workflow_organization_party_id=str(organization_id),
            verification_code_secret="verification-secret",
            composio_api_key="composio-key",
            workflow_composio_user_id="broker-user",
        ),
    )
    monkeypatch.setattr(
        chat_route,
        "_workflow_telemetry_services",
        lambda *_args: (object(), object()),
    )
    monkeypatch.setattr(chat_route, "get_workflow_retrieval", lambda _settings: Retrieval())
    monkeypatch.setattr(chat_route, "get_workflow_interaction_toolbox", lambda _settings: Toolbox())

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
    context = recorded[0][0]
    assert context.cause_type == "ui_action"
    assert context.trusted_workflow_id == workflow_id
    assert context.loaded_packet is not None
    assert invoked[0][0] == "approve_job"
    assert invoked[0][1]["expected_draft_revision_id"] == str(draft_id)
    assert invoked[0][2] is context

    revised = await chat_route.approve_exact_email(
        ChatApprovalCommand(
            sender_phone="+14165550101",
            cause_id="ui-approval-revision-1",
            workflow_id=workflow_id,
            job_id=job_id,
            expected_draft_revision_id=draft_id,
            revised_email={
                "to": ["john@example.com"],
                "subject": "Updated renewal",
                "body": "Dear John, the renewal email has been updated.",
            },
        )
    )

    assert revised.job_id == revised_job_id
    assert invoked[1][0] == "revise_and_approve_email"
    assert invoked[1][1]["email"] == {
        "to": ["john@example.com"],
        "cc": [],
        "bcc": [],
        "subject": "Updated renewal",
        "body": "Dear John, the renewal email has been updated.",
    }


async def test_direct_approval_hides_stale_domain_details(monkeypatch):
    actor_id = UUID("10000000-0000-0000-0000-000000000001")
    organization_id = UUID("20000000-0000-0000-0000-000000000001")
    workflow_id = UUID("40000000-0000-0000-0000-000000000001")
    job_id = UUID("50000000-0000-0000-0000-000000000001")
    draft_id = UUID("50000000-0000-0000-0000-000000000002")

    class Retrieval:
        async def read_workflow_packet(self, _context, _workflow_id):
            return SimpleNamespace(workflow=SimpleNamespace(workflow_id=workflow_id))

    class Toolbox:
        async def record_interaction_cause(self, _context, _content):
            return None

        async def invoke(self, _name, _arguments, _context):
            return SimpleNamespace(
                success=False,
                payload={
                    "code": "stale_approval_target",
                    "message": "Send Job does not exist",
                },
            )

    monkeypatch.setattr(
        chat_route,
        "get_settings",
        lambda: SimpleNamespace(
            interaction_mode="workflow",
            database_url="postgresql+psycopg://unused",
            workflow_cursor_secret="cursor-secret",
            workflow_organization_party_id=str(organization_id),
            verification_code_secret="verification-secret",
            composio_api_key="composio-key",
            workflow_composio_user_id="broker-user",
        ),
    )
    monkeypatch.setattr(
        chat_route,
        "_workflow_telemetry_services",
        lambda *_args: (object(), object()),
    )
    monkeypatch.setattr(chat_route, "get_workflow_retrieval", lambda _settings: Retrieval())
    monkeypatch.setattr(chat_route, "get_workflow_interaction_toolbox", lambda _settings: Toolbox())

    async def find_party(_database, _phone):
        return ResolvedSmsParty(
            party_id=actor_id,
            display_name="Carlos Broker",
            phone="+14165550101",
        )

    monkeypatch.setattr(chat_route, "find_sms_party", find_party)

    with pytest.raises(HTTPException) as captured:
        await chat_route.approve_exact_email(
            ChatApprovalCommand(
                sender_phone="+14165550101",
                cause_id="ui-approval-stale",
                workflow_id=workflow_id,
                job_id=job_id,
                expected_draft_revision_id=draft_id,
            )
        )

    assert captured.value.status_code == 409
    assert captured.value.detail == (
        "This approval is no longer available. Refresh the conversation and review the latest "
        "email."
    )
    assert "Job" not in captured.value.detail
