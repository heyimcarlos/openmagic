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
    NotificationLifecycleError,
    ReportRunResultCommand,
    RunResult,
    RunResultConflictError,
    StaleRunError,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowRetrieval,
)
from server.workflows.identity_models import WorkflowParticipantRoleRow
from server.workflows.models import (
    NotificationRow,
    WorkflowEventRow,
    WorkflowJobRow,
    WorkflowJobRunRow,
)
from server.workflows.worker import NotificationWorker, WorkflowWorker


def claim_command(worker_id: str = "worker-one") -> ClaimWorkflowJobCommand:
    return ClaimWorkflowJobCommand(
        worker_id=worker_id,
        application_build="test-build",
        lease_duration=timedelta(minutes=5),
        executor_keys=("renewal_email_drafter",),
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


@pytest.mark.parametrize("run_status", ["cancelled", "abandoned"])
async def test_terminal_run_cannot_submit_late_result(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
    run_status: str,
):
    await control_plane.create_workflow(create_command())
    packet = await control_plane.claim_job(claim_command())
    assert packet is not None
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowJobRunRow)
            .where(WorkflowJobRunRow.id == packet.run_id)
            .values(status=run_status, finished_at=datetime.now(UTC))
        )
        await connection.execute(
            sa.update(WorkflowJobRow)
            .where(WorkflowJobRow.id == packet.job_id)
            .values(status="cancelled" if run_status == "cancelled" else "queued")
        )
    await engine.dispose()

    with pytest.raises(StaleRunError):
        await control_plane.report_run_result(
            ReportRunResultCommand(run_id=packet.run_id, result=successful_draft())
        )


async def test_claim_revalidates_current_broker_authority(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    created = await control_plane.create_workflow(create_command())
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowParticipantRoleRow)
            .where(WorkflowParticipantRoleRow.workflow_id == created.workflow.id)
            .values(revoked_at=datetime.now(UTC))
        )
    await engine.dispose()

    assert await control_plane.claim_job(claim_command()) is None
    trace = await control_plane.read_workflow_trace(created.workflow.id, create_command().context)
    assert all(job.attempts == 0 for job in trace.jobs)
    assert trace.runs == ()


async def test_retryable_draft_failure_uses_persisted_attempt_budget(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    created = await control_plane.create_workflow(create_command())
    first = await control_plane.claim_job(claim_command())
    assert first is not None
    failure = RunResult(
        outcome="failed",
        evidence=({"type": "agent_output_rejected"},),
        error={"code": "invalid_draft_output"},
    )
    await control_plane.report_run_result(
        ReportRunResultCommand(run_id=first.run_id, result=failure)
    )
    trace = await control_plane.read_workflow_trace(created.workflow.id, create_command().context)
    draft = next(job for job in trace.jobs if job.kind == DRAFT_RENEWAL_EMAIL_KIND)
    assert draft.status == "queued"
    assert draft.attempts == 1

    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowJobRow)
            .where(WorkflowJobRow.id == draft.id)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    await engine.dispose()
    second = await control_plane.claim_job(claim_command())
    assert second is not None
    await control_plane.report_run_result(
        ReportRunResultCommand(run_id=second.run_id, result=failure)
    )
    trace = await control_plane.read_workflow_trace(created.workflow.id, create_command().context)
    draft = next(job for job in trace.jobs if job.kind == DRAFT_RENEWAL_EMAIL_KIND)
    assert draft.status == "failed"
    assert draft.attempts == 2


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
        executors={"renewal_email_drafter": factory},
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


async def test_draft_only_worker_leaves_unsupported_send_job_unclaimed(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    created = await control_plane.create_workflow(create_command())
    draft_packet = await control_plane.claim_job(claim_command())
    assert draft_packet is not None
    await control_plane.report_run_result(
        ReportRunResultCommand(run_id=draft_packet.run_id, result=successful_draft())
    )
    trace = await control_plane.read_workflow_trace(created.workflow.id, create_command().context)
    send = next(job for job in trace.jobs if job.kind != DRAFT_RENEWAL_EMAIL_KIND)
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowJobRow).where(WorkflowJobRow.id == send.id).values(status="queued")
        )
        await connection.execute(
            sa.insert(WorkflowEventRow).values(
                workflow_id=created.workflow.id,
                job_id=send.id,
                event_type="approval_granted",
                actor_type="party",
                actor_id=str(create_command().context.actor_party_id),
                cause_type="message",
                cause_id="approval-fixture",
                data={},
            )
        )
    await engine.dispose()
    worker = WorkflowWorker(
        control_plane=control_plane,
        executors={"renewal_email_drafter": _DraftRuntimeFactory()},
        worker_id="draft-only-worker",
        application_build="test-build",
    )

    assert await worker.run_once() is None
    trace = await control_plane.read_workflow_trace(created.workflow.id, create_command().context)
    send = next(job for job in trace.jobs if job.id == send.id)
    assert send.status == "queued"
    assert send.attempts == 0
    assert len(trace.runs) == 1


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


class _FailingNotificationInteraction:
    async def handle(self, *_ids: UUID) -> None:
        raise RuntimeError("delivery failed")


class _FailingNotificationInteractionFactory:
    @asynccontextmanager
    async def create(self):
        yield _FailingNotificationInteraction()


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
            delivery_attempt=delivered.delivery_attempt,
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


async def test_notification_failure_requeues_and_expired_claim_is_fenced(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    await control_plane.create_workflow(create_command())
    packet = await control_plane.claim_job(claim_command())
    assert packet is not None
    await control_plane.report_run_result(
        ReportRunResultCommand(run_id=packet.run_id, result=successful_draft())
    )
    failing_worker = NotificationWorker(
        control_plane=control_plane,
        interactions=_FailingNotificationInteractionFactory(),
        worker_id="failed-delivery-worker",
    )
    with pytest.raises(RuntimeError):
        await failing_worker.run_once()

    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        notification_id, status, attempts = (
            await connection.execute(
                sa.select(NotificationRow.id, NotificationRow.status, NotificationRow.attempts)
            )
        ).one()
        assert status == "queued"
        assert attempts == 1
        await connection.execute(
            sa.update(NotificationRow)
            .where(NotificationRow.id == notification_id)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    first = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="crashed-worker",
            lease_duration=timedelta(minutes=5),
        )
    )
    assert first is not None
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(NotificationRow)
            .where(NotificationRow.id == first.notification_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert (
        await control_plane.claim_notification(
            ClaimNotificationCommand(worker_id="new-worker", lease_duration=timedelta(minutes=5))
        )
        is None
    )
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(NotificationRow)
            .where(NotificationRow.id == first.notification_id)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    second = await control_plane.claim_notification(
        ClaimNotificationCommand(worker_id="new-worker", lease_duration=timedelta(minutes=5))
    )
    assert second is not None
    assert second.delivery_attempt == first.delivery_attempt + 1
    with pytest.raises(NotificationLifecycleError):
        await control_plane.acknowledge_notification(
            AcknowledgeNotificationCommand(
                notification_id=first.notification_id,
                worker_id="crashed-worker",
                delivery_attempt=first.delivery_attempt,
            )
        )
    delivered = await control_plane.acknowledge_notification(
        AcknowledgeNotificationCommand(
            notification_id=second.notification_id,
            worker_id="new-worker",
            delivery_attempt=second.delivery_attempt,
        )
    )
    assert delivered == second
    with pytest.raises(NotificationLifecycleError):
        await control_plane.acknowledge_notification(
            AcknowledgeNotificationCommand(
                notification_id=second.notification_id,
                worker_id="other-worker",
                delivery_attempt=second.delivery_attempt,
            )
        )
    await engine.dispose()


class _ApprovalPresenter:
    def __init__(self) -> None:
        self.effects: list[dict[str, object]] = []

    async def present(
        self,
        _notification_id: UUID,
        _destination_party_id: UUID,
        effect: dict[str, object],
    ) -> None:
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
        control_plane=control_plane,
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
