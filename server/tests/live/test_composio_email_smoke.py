"""Credentialed proof of one exact approved Gmail External Effect."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from composio import Composio
from pydantic import BaseModel, ConfigDict, EmailStr, SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.agents.interaction_agent.workflow_notifications import FreshWorkflowInteractionFactory
from server.config import Settings
from server.tests.live.agentmail import AgentMailRecipient
from server.workflows import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    RENEWAL_OUTREACH_KIND,
    AcknowledgeNotificationCommand,
    ApproveWorkflowJobCommand,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    ComposioGmailSendAdapter,
    ComposioMailboxBinding,
    CreateWorkflowCommand,
    EmailSendEffectV1,
    NotificationWorker,
    RecordInteractionCauseCommand,
    ReportRunResultCommand,
    RunResult,
    StaticWorkflowAuthority,
    WorkflowCommandContext,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowJobProposal,
    WorkflowProposal,
    WorkflowRetrieval,
    WorkflowWorker,
    default_workflow_registry,
)
from server.workflows.identity_models import (
    OrganizationMembershipRow,
    PartyIdentifierRow,
    PartyRow,
)


class _LiveSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    composio_api_key: SecretStr
    composio_auth_config_id: str
    composio_user_id: str
    agentmail_api_key: SecretStr
    sender: EmailStr
    recipient: EmailStr

    @classmethod
    def from_environment(cls) -> _LiveSettings:
        names = {
            "composio_api_key": "COMPOSIO_API_KEY",
            "composio_auth_config_id": "COMPOSIO_GMAIL_AUTH_CONFIG_ID",
            "composio_user_id": "OPENMAGIC_WORKFLOW_COMPOSIO_USER_ID",
            "agentmail_api_key": "AGENTMAIL_API_KEY",
            "sender": "OPENMAGIC_LIVE_EMAIL_SENDER",
            "recipient": "OPENMAGIC_LIVE_EMAIL_RECIPIENT",
        }
        values = {field: os.getenv(name) for field, name in names.items()}
        missing = [name for field, name in names.items() if not values[field]]
        if missing:
            raise RuntimeError(f"Live email smoke requires: {', '.join(sorted(missing))}")
        return cls.model_validate(values)


class _UnusedApprovalPresenter:
    async def present(
        self,
        notification_id: UUID,
        destination_party_id: UUID,
        effect: dict[str, object],
    ) -> str:
        del notification_id, destination_party_id, effect
        raise AssertionError("send confirmation must not present another approval")


class _ConversationSink:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def record_reply_once(self, _delivery_id: str, message: str) -> bool:
        self.messages.append(message)
        return True


async def _seed_broker_identity(
    database_url: str,
    *,
    broker_id: UUID,
    organization_id: UUID,
    mailbox_id: UUID,
    sender: EmailStr,
) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            session.add_all(
                [
                    PartyRow(id=broker_id, kind="person", display_name="Live Smoke Broker"),
                    PartyRow(
                        id=organization_id,
                        kind="organization",
                        display_name="Live Smoke Organization",
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    PartyIdentifierRow(
                        id=mailbox_id,
                        party_id=broker_id,
                        kind="email",
                        value=str(sender),
                        verified_at=datetime.now(UTC),
                    ),
                    OrganizationMembershipRow(
                        person_party_id=broker_id,
                        organization_party_id=organization_id,
                        granted_at=datetime.now(UTC),
                    ),
                ]
            )
    finally:
        await engine.dispose()


def _assert_active_gmail_connection(client: Composio, settings: _LiveSettings) -> None:
    try:
        accounts = client.connected_accounts.list(
            auth_config_ids=[settings.composio_auth_config_id],
            statuses=["ACTIVE"],
        ).items
    except Exception:
        raise RuntimeError("Composio Gmail connection preflight failed") from None
    matches = [
        account
        for account in accounts
        if account.toolkit.slug == "gmail" and account.user_id == settings.composio_user_id
    ]
    if len(matches) != 1:
        raise RuntimeError("Live email smoke requires one matching active Gmail connection")


@pytest.mark.skipif(
    os.getenv("OPENMAGIC_RUN_LIVE_EMAIL_SMOKE") != "1",
    reason="credentialed live email smoke is opt-in",
)
@pytest.mark.timeout(180)
@pytest.mark.usefixtures("clean_workflow_database")
async def test_exact_approved_email_reaches_the_authorized_recipient(
    migrated_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _LiveSettings.from_environment()
    client = Composio(api_key=settings.composio_api_key.get_secret_value())
    _assert_active_gmail_connection(client, settings)

    async with AgentMailRecipient(settings.agentmail_api_key, settings.recipient) as recipient:
        await _run_live_email_acceptance(
            settings=settings,
            client=client,
            recipient=recipient,
            database_url=migrated_postgres_url,
            monkeypatch=monkeypatch,
        )


async def _run_live_email_acceptance(
    *,
    settings: _LiveSettings,
    client: Composio,
    recipient: AgentMailRecipient,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_message_ids = await recipient.message_ids()
    broker_id, organization_id, mailbox_id = uuid4(), uuid4(), uuid4()
    await _seed_broker_identity(
        database_url,
        broker_id=broker_id,
        organization_id=organization_id,
        mailbox_id=mailbox_id,
        sender=settings.sender,
    )

    database = WorkflowDatabase(database_url)
    try:
        context = WorkflowCommandContext(
            actor_party_id=broker_id,
            organization_party_id=organization_id,
            cause_type="message",
            cause_id=f"live-request:{uuid4()}",
        )
        control_plane = WorkflowControlPlane(
            database=database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(
                grants={(broker_id, organization_id, RENEWAL_OUTREACH_KIND)}
            ),
        )
        created = await control_plane.create_workflow(
            CreateWorkflowCommand(
                context=context,
                proposal=WorkflowProposal(
                    kind=RENEWAL_OUTREACH_KIND,
                    objective="Live approved renewal email acceptance",
                    input={"renewal_period": "2026"},
                    jobs=(
                        WorkflowJobProposal(
                            key="draft",
                            kind=DRAFT_RENEWAL_EMAIL_KIND,
                            input={"recipient_name": "Live Smoke", "renewal_period": "2026"},
                        ),
                        WorkflowJobProposal(
                            key="send",
                            kind=GMAIL_SEND_EMAIL_KIND,
                            input={
                                "sender_mailbox": str(settings.sender),
                                "to": [str(settings.recipient)],
                                "subject": {"job_output": "draft", "field": "subject"},
                                "body": {"job_output": "draft", "field": "body"},
                            },
                            depends_on=("draft",),
                        ),
                    ),
                ),
            )
        )
        created_send_job = next(job for job in created.jobs if job.kind == GMAIL_SEND_EMAIL_KIND)
        correlation = f"OpenMagic live acceptance {created_send_job.id}"

        draft_run = await control_plane.claim_job(
            ClaimWorkflowJobCommand(
                worker_id="live-draft-worker",
                application_build="live-email-smoke",
                lease_duration=timedelta(minutes=5),
                executor_keys=("renewal_email_drafter",),
            )
        )
        assert draft_run is not None
        await control_plane.report_run_result(
            ReportRunResultCommand(
                run_id=draft_run.run_id,
                result=RunResult(
                    outcome="succeeded",
                    data={
                        "subject": correlation,
                        "body": "This is an authorized OpenMagic live integration test.",
                    },
                    evidence=({"type": "live_smoke_draft"},),
                ),
            )
        )

        approval_notification = await control_plane.claim_notification(
            ClaimNotificationCommand(
                worker_id="live-approval-presenter",
                lease_duration=timedelta(minutes=5),
            )
        )
        assert approval_notification is not None
        presentation = await control_plane.resolve_notification_presentation(
            approval_notification.notification_id,
            approval_notification.workflow_event_id,
            approval_notification.workflow_id,
            "live-approval-presenter",
            approval_notification.delivery_attempt,
        )
        if presentation.effect.get("subject") != correlation:
            raise AssertionError("Presented email does not carry the Send Job correlation")
        approved_effect = EmailSendEffectV1.model_validate(presentation.effect)
        await control_plane.acknowledge_notification(
            AcknowledgeNotificationCommand(
                notification_id=approval_notification.notification_id,
                worker_id="live-approval-presenter",
                delivery_attempt=approval_notification.delivery_attempt,
            )
        )

        approval_context = context.model_copy(update={"cause_id": f"live-approval:{uuid4()}"})
        await control_plane.record_interaction_cause(
            RecordInteractionCauseCommand(
                context=approval_context,
                content="Yes, send this exact live integration test email",
            )
        )
        await control_plane.approve_job(
            ApproveWorkflowJobCommand(
                context=approval_context,
                job_id=presentation.send_job_id,
                expected_draft_revision_id=presentation.draft_job_id,
            )
        )

        adapter = ComposioGmailSendAdapter(
            client=client,
            binding=ComposioMailboxBinding(
                sender_mailbox_id=mailbox_id,
                expected_sender_address=settings.sender,
                composio_user_id=settings.composio_user_id,
            ),
        )
        execution = await WorkflowWorker(
            control_plane=control_plane,
            executors={},
            email_adapters={"composio_gmail_send": adapter},
            worker_id="live-send-worker",
            application_build="live-email-smoke",
        ).run_once()
        assert execution is not None

        calls = 0

        async def controlled_notification_llm(self, _system_prompt, _messages):
            nonlocal calls
            calls += 1
            if calls == 1:
                name = "read_workflow_packet"
                arguments = {"workflow_id": str(created.workflow.id)}
            elif calls == 2:
                name = "present_status_update"
                arguments = {}
            else:
                return {"choices": [{"message": {"content": "Done.", "tool_calls": []}}]}
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": name,
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

        conversation = _ConversationSink()
        monkeypatch.setattr(InteractionAgentRuntime, "_make_llm_call", controlled_notification_llm)
        monkeypatch.setattr(
            "server.agents.interaction_agent.workflow_notifications.get_conversation_log",
            lambda: conversation,
        )
        delivered = await NotificationWorker(
            control_plane=control_plane,
            interactions=FreshWorkflowInteractionFactory(
                control_plane=control_plane,
                retrieval=WorkflowRetrieval(database=database, cursor_secret=b"live-email-smoke"),
                presenter=_UnusedApprovalPresenter(),
                settings=Settings(openrouter_api_key="controlled-live-smoke"),
                organization_party_id=organization_id,
            ),
            worker_id="live-confirmation-worker",
        ).run_once()
        assert delivered is not None
        assert conversation.messages == ["The renewal email was sent successfully."]

        trace = await control_plane.read_workflow_trace(created.workflow.id, context)
        send_job = next(job for job in trace.jobs if job.kind == GMAIL_SEND_EMAIL_KIND)
        send_run = next(run for run in trace.runs if run.job_id == send_job.id)
        event_types = [event.event_type for event in trace.events]
        send_notification = next(
            notification
            for notification in trace.notifications
            if notification.kind == "send_confirmed"
        )

        assert adapter.execute_call_count == 1
        assert send_job.id == created_send_job.id
        assert event_types.count("approval_granted") == 1
        assert event_types.count("external_effect_dispatch_started") == 1
        assert event_types.count("email_send_succeeded") == 1
        assert event_types.count("workflow_completed") == 1
        assert send_run.status == "succeeded"
        assert send_job.status == "succeeded"
        assert send_job.output is not None
        assert send_job.output.get("acknowledged") is True
        assert send_job.output.get("message_id")
        assert trace.workflow.status == "completed"
        assert send_notification.status == "delivered"
        assert await recipient.wait_for_exactly_one_message(
            effect=approved_effect,
            previous_ids=previous_message_ids,
            wait_limit=timedelta(seconds=90),
        )
    finally:
        await database.dispose()
