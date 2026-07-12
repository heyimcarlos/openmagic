from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from server.agents.interaction_agent.workflow_notifications import (
    FreshWorkflowInteractionFactory,
)
from server.tests.workflows.factories import create_command
from server.workflows import (
    DRAFT_RENEWAL_EMAIL_KIND,
    AcknowledgeNotificationCommand,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    ReportRunResultCommand,
    RunResult,
    RunResultConflictError,
    StaleRunError,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowRetrieval,
)
from server.workflows.models import WorkflowJobRunRow
from server.workflows.worker import NotificationWorker, WorkflowWorker


def claim_command(worker_id: str = "worker-one") -> ClaimWorkflowJobCommand:
    return ClaimWorkflowJobCommand(
        worker_id=worker_id,
        application_build="test-build",
        lease_duration=timedelta(minutes=5),
    )


def successful_draft() -> RunResult:
    return RunResult(
        outcome="succeeded",
        data={
            "subject": "Your 2026 policy renewal",
            "body": "Hello John Smith,\n\nLet's review your 2026 renewal options.",
        },
        evidence=({"type": "agent_output_validated"},),
    )


async def test_concurrent_claimers_create_one_run_and_count_one_attempt(
    control_plane: WorkflowControlPlane,
):
    created = await control_plane.create_workflow(create_command())

    first, second = await asyncio.gather(
        control_plane.claim_job(claim_command("worker-one")),
        control_plane.claim_job(claim_command("worker-two")),
    )

    packets = [packet for packet in (first, second) if packet is not None]
    assert len(packets) == 1
    packet = packets[0]
    assert packet.workflow_id == created.workflow.id
    assert packet.job_kind == DRAFT_RENEWAL_EMAIL_KIND
    assert packet.execution_strategy == "fresh_execution_agent"
    assert packet.runtime_instance_id is not None
    assert set(packet.input) == {"recipient_name", "renewal_period"}

    trace = await control_plane.read_workflow_trace(created.workflow.id, create_command().context)
    draft = next(job for job in trace.jobs if job.kind == DRAFT_RENEWAL_EMAIL_KIND)
    assert draft.status == "running"
    assert draft.attempts == 1
    assert len(trace.runs) == 1
    assert trace.runs[0].runtime_instance_id == packet.runtime_instance_id
    assert [event.event_type for event in trace.events] == [
        "workflow_jobs_proposed",
        "run_started",
    ]


async def test_success_publishes_frozen_draft_event_and_notification_atomically(
    control_plane: WorkflowControlPlane,
):
    created = await control_plane.create_workflow(create_command())
    packet = await control_plane.claim_job(claim_command())
    assert packet is not None
    command = ReportRunResultCommand(run_id=packet.run_id, result=successful_draft())

    committed = await control_plane.report_run_result(command)
    replay = await control_plane.report_run_result(command)

    assert replay == committed
    trace = await control_plane.read_workflow_trace(created.workflow.id, create_command().context)
    draft = next(job for job in trace.jobs if job.kind == DRAFT_RENEWAL_EMAIL_KIND)
    send = next(job for job in trace.jobs if job.kind != DRAFT_RENEWAL_EMAIL_KIND)
    assert draft.status == "succeeded"
    assert draft.output == successful_draft().data
    assert send.status == "waiting"
    assert send.waiting_reasons == ("exact_approval",)
    assert [run.status for run in trace.runs] == ["succeeded"]
    assert [event.event_type for event in trace.events].count("draft_ready") == 1
    assert len(trace.notifications) == 1
    assert trace.notifications[0].kind == "approval_required"
    assert trace.notifications[0].status == "queued"

    changed = command.model_copy(
        update={
            "result": successful_draft().model_copy(
                update={"data": {"subject": "changed", "body": "changed"}}
            )
        }
    )
    with pytest.raises(RunResultConflictError):
        await control_plane.report_run_result(changed)


async def test_expired_run_cannot_publish_output(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    created = await control_plane.create_workflow(create_command())
    packet = await control_plane.claim_job(claim_command())
    assert packet is not None
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowJobRunRow)
            .where(WorkflowJobRunRow.id == packet.run_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    await engine.dispose()

    with pytest.raises(StaleRunError):
        await control_plane.report_run_result(
            ReportRunResultCommand(run_id=packet.run_id, result=successful_draft())
        )

    trace = await control_plane.read_workflow_trace(created.workflow.id, create_command().context)
    draft = next(job for job in trace.jobs if job.kind == DRAFT_RENEWAL_EMAIL_KIND)
    assert draft.status == "running"
    assert draft.output is None


class _DraftRuntime:
    def __init__(self, runtime_instance_id: UUID, seen: list[tuple[UUID, dict[str, object]]]):
        self.runtime_instance_id = runtime_instance_id
        self._seen = seen

    async def execute(self, execution_input: dict[str, object]) -> RunResult:
        self._seen.append((self.runtime_instance_id, execution_input))
        return successful_draft()


class _DraftRuntimeFactory:
    def __init__(self) -> None:
        self.seen: list[tuple[UUID, dict[str, object]]] = []
        self.live: set[UUID] = set()

    @asynccontextmanager
    async def create(self, runtime_instance_id: UUID):
        assert runtime_instance_id not in self.live
        self.live.add(runtime_instance_id)
        try:
            yield _DraftRuntime(runtime_instance_id, self.seen)
        finally:
            self.live.remove(runtime_instance_id)


async def test_worker_uses_disposable_registry_selected_runtime(
    control_plane: WorkflowControlPlane,
):
    created = await control_plane.create_workflow(create_command())
    factory = _DraftRuntimeFactory()
    worker = WorkflowWorker(
        control_plane=control_plane,
        draft_runtimes=factory,
        worker_id="draft-worker",
        application_build="test-build",
    )

    packet = await worker.run_once()

    assert packet is not None
    assert factory.seen == [(packet.runtime_instance_id, packet.input)]
    assert factory.live == set()
    trace = await control_plane.read_workflow_trace(created.workflow.id, create_command().context)
    assert next(job for job in trace.jobs if job.kind == DRAFT_RENEWAL_EMAIL_KIND).status == (
        "succeeded"
    )


class _NotificationInteraction:
    def __init__(self, calls: list[tuple[UUID, UUID, UUID]]) -> None:
        self._calls = calls

    async def handle(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
    ) -> None:
        self._calls.append((notification_id, workflow_event_id, workflow_id))


class _NotificationInteractionFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID, UUID]] = []
        self.live = 0

    @asynccontextmanager
    async def create(self):
        self.live += 1
        try:
            yield _NotificationInteraction(self.calls)
        finally:
            self.live -= 1


async def test_notification_hands_off_only_ids_and_acknowledges_idempotently(
    control_plane: WorkflowControlPlane,
):
    created = await control_plane.create_workflow(create_command())
    packet = await control_plane.claim_job(claim_command())
    assert packet is not None
    await control_plane.report_run_result(
        ReportRunResultCommand(run_id=packet.run_id, result=successful_draft())
    )
    interactions = _NotificationInteractionFactory()
    worker = NotificationWorker(
        control_plane=control_plane,
        interactions=interactions,
        worker_id="notification-worker",
    )

    delivered = await worker.run_once()

    assert delivered is not None
    assert interactions.calls == [
        (
            delivered.notification_id,
            delivered.workflow_event_id,
            delivered.workflow_id,
        )
    ]
    assert interactions.live == 0
    replay = await control_plane.acknowledge_notification(
        AcknowledgeNotificationCommand(
            notification_id=delivered.notification_id,
            worker_id="notification-worker",
        )
    )
    assert replay == delivered
    assert (
        await control_plane.claim_notification(
            ClaimNotificationCommand(
                worker_id="other-worker",
                lease_duration=timedelta(minutes=5),
            )
        )
        is None
    )
    trace = await control_plane.read_workflow_trace(created.workflow.id, create_command().context)
    assert trace.notifications[0].status == "delivered"


class _ApprovalPresenter:
    def __init__(self) -> None:
        self.effects: list[dict[str, object]] = []

    async def present(self, effect: dict[str, object]) -> None:
        self.effects.append(effect)


async def test_fresh_interaction_reloads_packet_and_presents_exact_send_input(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    created = await control_plane.create_workflow(create_command())
    claimed = await control_plane.claim_job(claim_command())
    assert claimed is not None
    await control_plane.report_run_result(
        ReportRunResultCommand(run_id=claimed.run_id, result=successful_draft())
    )
    database = WorkflowDatabase(migrated_postgres_url)
    retrieval = WorkflowRetrieval(database=database, cursor_secret=b"notification-test")
    presenter = _ApprovalPresenter()
    factory = FreshWorkflowInteractionFactory(
        database=database,
        retrieval=retrieval,
        presenter=presenter,
    )
    worker = NotificationWorker(
        control_plane=control_plane,
        interactions=factory,
        worker_id="notification-worker",
    )

    delivered = await worker.run_once()

    assert delivered is not None
    assert delivered.workflow_id == created.workflow.id
    assert presenter.effects == [
        {
            "sender_mailbox": "broker@acme.example",
            "to": ["john@example.com"],
            "cc": [],
            "bcc": [],
            "subject": "Your 2026 policy renewal",
            "body": "Hello John Smith,\n\nLet's review your 2026 renewal options.",
        }
    ]
    async with factory.create() as first:
        first_runtime_id = first.runtime_instance_id
    async with factory.create() as second:
        second_runtime_id = second.runtime_instance_id
    assert first_runtime_id != second_runtime_id
    await database.dispose()
