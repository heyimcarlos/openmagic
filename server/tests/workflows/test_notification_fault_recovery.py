from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from server.agents.interaction_agent.workflow_notifications import (
    ConversationApprovalPresenter,
    FreshWorkflowInteractionFactory,
)
from server.config import Settings
from server.tests.workflows.factories import BROKER_ID, ORGANIZATION_ID, create_command
from server.workflows import (
    RENEWAL_OUTREACH_KIND,
    AcknowledgeNotificationCommand,
    CancelWorkflowCommand,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    NotificationLifecycleError,
    ReportRunResultCommand,
    RunResult,
    StaticWorkflowAuthority,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowInspectionContext,
    WorkflowRetrieval,
    default_workflow_registry,
)
from server.workflows.models import NotificationRow
from server.workflows.worker import NotificationWorker


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, duration: timedelta) -> None:
        self.current += duration


class ApprovalCompletion:
    def __init__(self, workflow_id: UUID) -> None:
        self.workflow_id = workflow_id
        self.first_messages: list[list[dict[str, Any]]] = []

    async def __call__(self, **request: Any) -> dict[str, Any]:
        messages = request["messages"]
        invoked = {
            call["function"]["name"]
            for message in messages
            for call in message.get("tool_calls", [])
        }
        if "read_workflow_packet" not in invoked:
            self.first_messages.append(json.loads(json.dumps(messages)))
            return _tool_call("read_workflow_packet", {"workflow_id": str(self.workflow_id)})
        if "present_approval_request" not in invoked:
            return _tool_call("present_approval_request", {})
        return {"choices": [{"message": {"content": "Presented.", "tool_calls": []}}]}


class CorrelatedReplyLog:
    def __init__(self) -> None:
        self.messages_by_delivery: dict[str, str] = {}
        self.calls = 0

    def record_reply_once(
        self,
        delivery_id: str,
        message: str,
        *,
        cause_id: str | None = None,
    ) -> bool:
        self.calls += 1
        if delivery_id in self.messages_by_delivery:
            return False
        self.messages_by_delivery[delivery_id] = message
        return True

    def load_transcript(self) -> str:
        raise AssertionError("fresh Notification runtime must not load conversation history")


class TrackingInteractionFactory:
    def __init__(self, delegate: FreshWorkflowInteractionFactory) -> None:
        self._delegate = delegate
        self.runtime_instance_ids: list[UUID] = []

    @asynccontextmanager
    async def create(self, worker_id: str, delivery_attempt: int):
        async with self._delegate.create(worker_id, delivery_attempt) as interaction:
            self.runtime_instance_ids.append(interaction.runtime_instance_id)
            yield interaction


async def test_delayed_notification_becomes_claimable_only_after_available_at(
    migrated_postgres_url: str,
    seeded_workflow_identity,
) -> None:
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(
            grants={(BROKER_ID, ORGANIZATION_ID, RENEWAL_OUTREACH_KIND)}
        ),
        notification_clock=clock,
    )
    await _publish_draft_notification(control_plane)
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(NotificationRow).values(available_at=clock.current + timedelta(minutes=5))
        )

    early = await control_plane.claim_notification(_notification_claim("early-worker"))
    clock.advance(timedelta(minutes=5))
    due = await control_plane.claim_notification(_notification_claim("due-worker"))

    assert early is None
    assert due is not None
    assert due.delivery_attempt == 1
    await engine.dispose()
    await database.dispose()


async def test_concurrent_claim_and_duplicate_ack_do_not_duplicate_delivery(
    migrated_postgres_url: str,
    seeded_workflow_identity,
) -> None:
    control_plane, database = _control_plane(migrated_postgres_url)
    created = await _publish_draft_notification(control_plane)

    first, second = await asyncio.gather(
        control_plane.claim_notification(_notification_claim("worker-one")),
        control_plane.claim_notification(_notification_claim("worker-two")),
    )

    packets = [packet for packet in (first, second) if packet is not None]
    assert len(packets) == 1
    packet = packets[0]
    worker_id = "worker-one" if first is not None else "worker-two"
    command = AcknowledgeNotificationCommand(
        notification_id=packet.notification_id,
        worker_id=worker_id,
        delivery_attempt=packet.delivery_attempt,
    )
    delivered = await control_plane.acknowledge_notification(command)
    duplicate = await control_plane.acknowledge_notification(command)

    assert duplicate == delivered
    trace = await control_plane.read_workflow_trace(created, create_command().context)
    assert trace.notifications[0].status == "delivered"
    assert trace.notifications[0].attempts == 1
    await database.dispose()


async def test_visible_reply_survives_lost_ack_without_duplicate_message(
    migrated_postgres_url: str,
    seeded_workflow_identity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    control_plane, database = _control_plane(migrated_postgres_url, clock)
    workflow_id = await _publish_draft_notification(control_plane)
    reply_log = CorrelatedReplyLog()
    monkeypatch.setattr(
        "server.agents.interaction_agent.workflow_notifications.get_conversation_log",
        lambda: reply_log,
    )
    factory = _fresh_factory(control_plane, database, workflow_id)

    first = await control_plane.claim_notification(_notification_claim("first-worker"))
    assert first is not None
    async with factory.create("first-worker", first.delivery_attempt) as interaction:
        await interaction.handle(
            first.notification_id,
            first.workflow_event_id,
            first.workflow_id,
        )

    clock.advance(timedelta(minutes=5))
    stale_acknowledgement = AcknowledgeNotificationCommand(
        notification_id=first.notification_id,
        worker_id="first-worker",
        delivery_attempt=first.delivery_attempt,
    )
    with pytest.raises(NotificationLifecycleError, match="lease is stale"):
        await control_plane.acknowledge_notification(stale_acknowledgement)
    assert await control_plane.claim_notification(_notification_claim("recovery-scan")) is None
    clock.advance(timedelta(seconds=1))
    second = await control_plane.claim_notification(_notification_claim("recovery-worker"))
    assert second is not None
    with pytest.raises(NotificationLifecycleError, match="lease is stale"):
        await control_plane.acknowledge_notification(stale_acknowledgement)
    async with factory.create("recovery-worker", second.delivery_attempt) as interaction:
        await interaction.handle(
            second.notification_id,
            second.workflow_event_id,
            second.workflow_id,
        )
    await control_plane.acknowledge_notification(
        AcknowledgeNotificationCommand(
            notification_id=second.notification_id,
            worker_id="recovery-worker",
            delivery_attempt=second.delivery_attempt,
        )
    )

    assert reply_log.calls == 2
    assert len(reply_log.messages_by_delivery) == 1
    trace = await control_plane.read_workflow_trace(workflow_id, create_command().context)
    notification = trace.notifications[0]
    assert notification.status == "delivered"
    assert notification.attempts == 2
    assert notification.last_error == "delivery_lease_expired"
    await database.dispose()


async def test_stale_approval_notification_exhausts_budget_without_user_message(
    migrated_postgres_url: str,
    seeded_workflow_identity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = MutableClock(datetime.now(UTC) + timedelta(seconds=1))
    control_plane, database = _control_plane(migrated_postgres_url, clock)
    workflow_id = await _publish_draft_notification(control_plane)
    await control_plane.cancel_workflow(
        CancelWorkflowCommand(
            context=create_command().context,
            workflow_id=workflow_id,
        )
    )
    reply_log = CorrelatedReplyLog()
    monkeypatch.setattr(
        "server.agents.interaction_agent.workflow_notifications.get_conversation_log",
        lambda: reply_log,
    )
    worker = NotificationWorker(
        control_plane=control_plane,
        interactions=_fresh_factory(control_plane, database, workflow_id),
        worker_id="notification-worker",
    )

    for backoff in (timedelta(seconds=1), timedelta(seconds=2)):
        with pytest.raises(NotificationLifecycleError):
            await worker.run_once()
        clock.advance(backoff)
    with pytest.raises(NotificationLifecycleError):
        await worker.run_once()

    trace = await control_plane.read_workflow_trace(workflow_id, create_command().context)
    notification = trace.notifications[0]
    assert notification.status == "failed"
    assert notification.attempts == notification.max_attempts == 3
    assert notification.last_error == "interaction_delivery_failed"
    assert reply_log.messages_by_delivery == {}
    await database.dispose()


async def test_notification_delivery_restarts_from_durable_identifiers_only(
    migrated_postgres_url: str,
    seeded_workflow_identity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_control_plane, initial_database = _control_plane(migrated_postgres_url)
    workflow_id = await _publish_draft_notification(initial_control_plane)
    await initial_database.dispose()

    restarted_control_plane, restarted_database = _control_plane(migrated_postgres_url)
    reply_log = CorrelatedReplyLog()
    monkeypatch.setattr(
        "server.agents.interaction_agent.workflow_notifications.get_conversation_log",
        lambda: reply_log,
    )
    completion = ApprovalCompletion(workflow_id)
    interactions = TrackingInteractionFactory(
        _fresh_factory(
            restarted_control_plane,
            restarted_database,
            workflow_id,
            completion=completion,
        )
    )
    worker = NotificationWorker(
        control_plane=restarted_control_plane,
        interactions=interactions,
        worker_id="restarted-worker",
    )

    delivered = await worker.run_once()

    assert delivered is not None
    assert delivered.workflow_id == workflow_id
    assert len(interactions.runtime_instance_ids) == 1
    assert len(reply_log.messages_by_delivery) == 1
    assert len(completion.first_messages) == 1
    assert len(completion.first_messages[0]) == 1
    assert "notification_id" in completion.first_messages[0][0]["content"]
    packet = await WorkflowRetrieval(
        database=restarted_database,
        cursor_secret=b"restart-evidence",
    ).read_workflow_packet(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        workflow_id,
    )
    assert packet.workflow.workflow_id == workflow_id
    trace = await restarted_control_plane.read_workflow_trace(
        workflow_id,
        create_command().context,
    )
    assert trace.notifications[0].status == "delivered"
    await restarted_database.dispose()


async def _publish_draft_notification(control_plane: WorkflowControlPlane) -> UUID:
    created = await control_plane.create_workflow(create_command())
    draft = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="draft-worker",
            application_build=os.getenv(
                "OPENMAGIC_EVAL_APPLICATION_BUILD",
                "notification-eval",
            ),
            lease_duration=timedelta(minutes=5),
            executor_keys=("renewal_email_drafter",),
        )
    )
    assert draft is not None
    await control_plane.report_run_result(
        ReportRunResultCommand(
            run_id=draft.run_id,
            result=RunResult(
                outcome="succeeded",
                data={"subject": "Renewal", "body": "Review this renewal."},
                evidence=({"type": "agent_output_validated"},),
            ),
        )
    )
    return created.workflow.id


def _control_plane(
    postgres_url: str,
    clock: MutableClock | None = None,
) -> tuple[WorkflowControlPlane, WorkflowDatabase]:
    database = WorkflowDatabase(postgres_url)
    return (
        WorkflowControlPlane(
            database=database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(
                grants={(BROKER_ID, ORGANIZATION_ID, RENEWAL_OUTREACH_KIND)}
            ),
            notification_clock=clock,
        ),
        database,
    )


def _fresh_factory(
    control_plane: WorkflowControlPlane,
    database: WorkflowDatabase,
    workflow_id: UUID,
    completion: ApprovalCompletion | None = None,
) -> FreshWorkflowInteractionFactory:
    return FreshWorkflowInteractionFactory(
        control_plane=control_plane,
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"notification-evidence"),
        presenter=ConversationApprovalPresenter(expected_party_id=BROKER_ID),
        settings=Settings(openrouter_api_key="test-key"),
        organization_party_id=ORGANIZATION_ID,
        completion=completion or ApprovalCompletion(workflow_id),
    )


def _tool_call(name: str, arguments: dict[str, object]) -> dict[str, Any]:
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


def _notification_claim(worker_id: str) -> ClaimNotificationCommand:
    return ClaimNotificationCommand(
        worker_id=worker_id,
        lease_duration=timedelta(minutes=5),
    )
