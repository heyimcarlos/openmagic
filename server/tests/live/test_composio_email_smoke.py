"""Credentialed proof of one exact approved Gmail External Effect."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from composio import Composio
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
    WorkflowWorker,
    default_workflow_registry,
)
from server.workflows.identity_models import (
    OrganizationMembershipRow,
    PartyIdentifierRow,
    PartyRow,
)

pytestmark = [
    pytest.mark.skipif(
        os.getenv("OPENMAGIC_RUN_LIVE_EMAIL_SMOKE") != "1",
        reason="credentialed live email smoke is opt-in",
    ),
    pytest.mark.timeout(180),
]


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


class _AgentMailInbox(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    inbox_id: str
    email: EmailStr


class _AgentMailInboxPage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    inboxes: tuple[_AgentMailInbox, ...]


class _AgentMailMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    message_id: str
    subject: str
    sender: object = Field(alias="from")


class _AgentMailMessagePage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    messages: tuple[_AgentMailMessage, ...]


class _AgentMailRecipient:
    def __init__(self, api_key: SecretStr, recipient: EmailStr) -> None:
        self._recipient = recipient
        self._client = httpx.AsyncClient(
            base_url="https://api.agentmail.to/v0",
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            timeout=20,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def message_ids(self) -> set[str]:
        return {message.message_id for message in await self._messages()}

    async def wait_for_message(
        self,
        *,
        subject: str,
        sender: EmailStr,
        previous_ids: set[str],
        wait_limit: timedelta,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + wait_limit.total_seconds()
        while True:
            for message in await self._messages():
                sender_shape = json.dumps(message.sender, sort_keys=True).lower()
                if (
                    message.message_id not in previous_ids
                    and message.subject == subject
                    and str(sender).lower() in sender_shape
                ):
                    return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(2)

    async def _messages(self) -> tuple[_AgentMailMessage, ...]:
        try:
            inboxes_response = await self._client.get("/inboxes")
            inboxes_response.raise_for_status()
            inboxes = _AgentMailInboxPage.model_validate(inboxes_response.json()).inboxes
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("AgentMail inbox response is unavailable or malformed") from None
        inbox = next((item for item in inboxes if item.email == self._recipient), None)
        if inbox is None:
            raise RuntimeError("Configured AgentMail recipient does not exist")
        try:
            messages_response = await self._client.get(f"/inboxes/{inbox.inbox_id}/messages")
            messages_response.raise_for_status()
            return _AgentMailMessagePage.model_validate(messages_response.json()).messages
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("AgentMail message response is unavailable or malformed") from None


class _ConfirmationInteraction:
    def __init__(
        self,
        control_plane: WorkflowControlPlane,
        worker_id: str,
        delivery_attempt: int,
        messages: list[str],
    ) -> None:
        self._control_plane = control_plane
        self._worker_id = worker_id
        self._delivery_attempt = delivery_attempt
        self._messages = messages

    async def handle(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
    ) -> None:
        status = await self._control_plane.resolve_notification_status(
            notification_id,
            workflow_event_id,
            workflow_id,
            self._worker_id,
            self._delivery_attempt,
        )
        self._messages.append(status.message)


class _ConfirmationInteractionFactory:
    def __init__(self, control_plane: WorkflowControlPlane) -> None:
        self._control_plane = control_plane
        self.messages: list[str] = []

    @asynccontextmanager
    async def create(
        self,
        worker_id: str,
        delivery_attempt: int,
    ) -> AsyncIterator[_ConfirmationInteraction]:
        yield _ConfirmationInteraction(
            self._control_plane,
            worker_id,
            delivery_attempt,
            self.messages,
        )


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


@pytest.mark.usefixtures("clean_workflow_database")
async def test_exact_approved_email_reaches_the_authorized_recipient(
    migrated_postgres_url: str,
) -> None:
    settings = _LiveSettings.from_environment()
    client = Composio(api_key=settings.composio_api_key.get_secret_value())
    _assert_active_gmail_connection(client, settings)

    recipient = _AgentMailRecipient(settings.agentmail_api_key, settings.recipient)
    previous_message_ids = await recipient.message_ids()
    broker_id, organization_id, mailbox_id = uuid4(), uuid4(), uuid4()
    await _seed_broker_identity(
        migrated_postgres_url,
        broker_id=broker_id,
        organization_id=organization_id,
        mailbox_id=mailbox_id,
        sender=settings.sender,
    )

    database = WorkflowDatabase(migrated_postgres_url)
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
    try:
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

        interactions = _ConfirmationInteractionFactory(control_plane)
        delivered = await NotificationWorker(
            control_plane=control_plane,
            interactions=interactions,
            worker_id="live-confirmation-worker",
        ).run_once()
        assert delivered is not None
        assert interactions.messages == ["The renewal email was sent successfully."]

        trace = await control_plane.read_workflow_trace(created.workflow.id, context)
        send_job = next(job for job in trace.jobs if job.kind == GMAIL_SEND_EMAIL_KIND)
        send_run = next(run for run in trace.runs if run.job_id == send_job.id)
        event_types = [event.event_type for event in trace.events]
        send_notification = next(
            notification
            for notification in trace.notifications
            if notification.kind == "send_confirmed"
        )

        assert adapter.invocation_count == 1
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
        assert await recipient.wait_for_message(
            subject=correlation,
            sender=settings.sender,
            previous_ids=previous_message_ids,
            wait_limit=timedelta(seconds=90),
        )
    finally:
        await recipient.close()
        await database.dispose()
