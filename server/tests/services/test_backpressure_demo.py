from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from server.config import Settings
from server.services.backpressure_demo import BackpressureDemoService
from server.tests.workflows.factories import BROKER_ID, ORGANIZATION_ID
from server.workflows import (
    AcknowledgeNotificationCommand,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    ReportRunResultCommand,
    RunResult,
    StaticWorkflowAuthority,
    WorkflowControlPlane,
    WorkflowDatabase,
    default_workflow_registry,
)

pytest_plugins = ("server.tests.workflows.conftest",)


def _settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        workflow_cursor_secret="demo-test-secret",
        workflow_broker_party_id=str(BROKER_ID),
        workflow_organization_party_id=str(ORGANIZATION_ID),
        demo_broker_email="broker@acme.example",
        demo_policyholder_email="john@example.com",
        interaction_mode="workflow",
    )


def _service(database_url: str) -> BackpressureDemoService:
    return BackpressureDemoService(
        _settings(database_url),
        worker_ids=lambda: ("workflow-worker:test",),
        max_worker_capacity=lambda: 8,
    )


async def test_job_burst_uses_atomic_workflow_commands_and_projects_queue_pressure(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    service = _service(migrated_postgres_url)

    snapshot = await service.enqueue_jobs(10)

    assert snapshot.counts.workflows == 5
    assert snapshot.scope.visible_workflows == 5
    assert snapshot.scope.total_workflows == 5
    assert snapshot.scope.truncated is False
    assert snapshot.counts.jobs == 10
    assert snapshot.counts.queued == 5
    assert snapshot.counts.waiting == 5
    assert snapshot.counts.running == 0
    assert snapshot.counts.succeeded == 0
    assert snapshot.counts.notifications_queued == 0
    assert snapshot.worker.configured_job_concurrency == 1
    assert snapshot.worker.configured_notification_concurrency == 1
    assert snapshot.worker.job_worker_ids == ("workflow-worker:test",)
    assert snapshot.worker.max_job_worker_capacity == 8
    assert snapshot.worker.liveness == "not_persisted"
    assert len(snapshot.jobs) == 10
    assert {job.kind for job in snapshot.jobs} == {
        "renewal_email.draft.v1",
        "gmail.send_email.v1",
    }
    assert {job.task_summary for job in snapshot.jobs if job.kind == "renewal_email.draft.v1"} == {
        "Draft the 2026 renewal for Demo Policyholder 1",
        "Draft the 2026 renewal for Demo Policyholder 2",
        "Draft the 2026 renewal for Demo Policyholder 3",
        "Draft the 2026 renewal for Demo Policyholder 4",
        "Draft the 2026 renewal for Demo Policyholder 5",
    }
    assert [event.type for event in snapshot.activity] == [
        "workflow_jobs_proposed",
    ] * 5

    await service.dispose()


async def test_snapshot_separates_run_completion_from_notification_delivery(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    settings = _settings(migrated_postgres_url)
    service = BackpressureDemoService(
        settings,
        worker_ids=lambda: ("workflow-worker:test",),
        max_worker_capacity=lambda: 8,
    )
    await service.enqueue_jobs(2)
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    packet = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="demo-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("renewal_email_drafter",),
        )
    )
    assert packet is not None
    await control_plane.report_run_result(
        ReportRunResultCommand(
            run_id=packet.run_id,
            result=RunResult(
                outcome="succeeded",
                data={"subject": "2026 renewal", "body": "Hello John"},
                evidence=({"type": "deterministic-test"},),
            ),
        )
    )

    snapshot = await service.snapshot()

    assert snapshot.counts.queued == 0
    assert snapshot.counts.succeeded == 1
    assert snapshot.counts.waiting == 1
    assert snapshot.counts.runs_succeeded == 1
    assert snapshot.counts.notifications_queued == 1
    assert snapshot.counts.notifications_delivered == 0
    assert snapshot.runs[0].runtime_instance_id == packet.runtime_instance_id
    assert snapshot.notifications[0].status == "queued"
    assert snapshot.latency.queue_claim_p50_ms is not None
    assert snapshot.latency.execution_p50_ms is not None
    assert [item.type for item in snapshot.activity[:3]] == [
        "notification_queued",
        "draft_ready",
        "run_started",
    ]

    await database.dispose()
    await service.dispose()


async def test_snapshot_projects_exact_approval_and_fresh_interaction_identity(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    service = _service(migrated_postgres_url)
    await service.enqueue_jobs(2)
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    draft = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="demo-worker",
            application_build="test-build",
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
                data={"subject": "2026 renewal", "body": "Hello John"},
                evidence=({"type": "deterministic-test"},),
            ),
        )
    )
    notification = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="notification-worker:test",
            lease_duration=timedelta(minutes=5),
        )
    )
    assert notification is not None
    interaction_runtime_id = uuid4()
    presentation = await control_plane.resolve_notification_presentation(
        notification.notification_id,
        notification.workflow_event_id,
        notification.workflow_id,
        "notification-worker:test",
        notification.delivery_attempt,
        interaction_runtime_id,
    )
    await control_plane.acknowledge_notification(
        AcknowledgeNotificationCommand(
            notification_id=notification.notification_id,
            worker_id="notification-worker:test",
            delivery_attempt=notification.delivery_attempt,
        )
    )

    snapshot = await service.snapshot()

    assert snapshot.notifications[0].interaction_runtime_instance_id == interaction_runtime_id
    assert snapshot.approval_requests[0].workflow_id == notification.workflow_id
    assert snapshot.approval_requests[0].job_id == presentation.send_job_id
    assert snapshot.approval_requests[0].draft_revision_id == presentation.draft_job_id
    assert snapshot.approval_requests[0].subject == "2026 renewal"
    assert snapshot.approval_requests[0].body == "Hello John"

    await database.dispose()
    await service.dispose()


async def test_snapshot_bounds_repeated_demo_history(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    service = _service(migrated_postgres_url)

    await service.enqueue_jobs(40)
    await service.enqueue_jobs(40)
    snapshot = await service.enqueue_jobs(40)

    assert snapshot.counts.workflows == 50
    assert snapshot.counts.jobs == 100
    assert snapshot.scope.visible_workflows == 50
    assert snapshot.scope.total_workflows == 60
    assert snapshot.scope.workflow_limit == 50
    assert snapshot.scope.truncated is True
    assert len(snapshot.jobs) == 100
    assert len(snapshot.activity) <= 80

    await service.dispose()
