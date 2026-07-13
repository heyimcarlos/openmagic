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
    RecordInteractionCauseCommand,
    ReportRunResultCommand,
    ReviseAndApproveWorkflowEmailCommand,
    RevisedEmailContent,
    RunResult,
    StaticWorkflowAuthority,
    WorkflowCommandContext,
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

    snapshot = await service.enqueue_workflows(5, "renewal")

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


async def test_mixed_workflow_burst_contains_distinct_workflow_and_job_shapes(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    service = _service(migrated_postgres_url)

    snapshot = await service.enqueue_workflows(6)

    assert snapshot.counts.workflows == 6
    assert {job.kind for job in snapshot.jobs} == {
        "renewal_email.draft.v1",
        "gmail.send_email.v1",
        "insurance_task.execute.v1",
    }
    assert {job.label for job in snapshot.jobs if job.kind == "insurance_task.execute.v1"} == {
        "Extract claim facts",
        "Assess claim routing",
        "Review policy coverage",
    }
    assert snapshot.counts.waiting > 0
    assert snapshot.counts.queued > 0

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
    await service.enqueue_workflows(1, "renewal")
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
    await service.enqueue_workflows(1, "renewal")
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


async def test_snapshot_projects_immutable_email_revision_boundary(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    service = _service(migrated_postgres_url)
    await service.enqueue_workflows(1, "renewal")
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
                data={"subject": "Original renewal", "body": "Original body"},
                evidence=({"type": "deterministic-test"},),
            ),
        )
    )
    before_revision = await service.snapshot()
    send = next(job for job in before_revision.jobs if job.status == "waiting")
    context = WorkflowCommandContext(
        actor_party_id=BROKER_ID,
        organization_party_id=ORGANIZATION_ID,
        cause_type="ui_action",
        cause_id=f"revision-test:{send.id}",
    )
    await control_plane.record_interaction_cause(
        RecordInteractionCauseCommand(
            context=context,
            content="Revise the reviewable renewal email",
        )
    )

    approval = await control_plane.revise_and_approve_email(
        ReviseAndApproveWorkflowEmailCommand(
            context=context,
            workflow_id=send.workflow_id,
            job_id=send.id,
            expected_draft_revision_id=draft.job_id,
            email=RevisedEmailContent(
                to=("john@example.com",),
                subject="Revised renewal",
                body="Revised body",
            ),
        )
    )

    snapshot = await service.snapshot()

    assert any(
        item.type == "workflow_work_revised" and item.boundary == "revise_and_approve_email"
        for item in snapshot.activity
    )
    revised_draft = next(job for job in snapshot.jobs if job.id == approval.draft_job_id)
    revised_send = next(job for job in snapshot.jobs if job.id == approval.job_id)
    assert revised_draft.revision == 2
    assert revised_send.revision == 2
    assert revised_send.status == "queued"
    assert next(job for job in snapshot.jobs if job.id == send.id).status == "cancelled"

    await database.dispose()
    await service.dispose()


async def test_completed_non_approval_work_notifies_the_interaction_agent(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    service = _service(migrated_postgres_url)
    await service.enqueue_workflows(1, "policy")
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
            executor_keys=("insurance_work_agent",),
        )
    )
    assert packet is not None
    await control_plane.report_run_result(
        ReportRunResultCommand(
            run_id=packet.run_id,
            result=RunResult(
                outcome="succeeded",
                data={
                    "title": "Coverage review ready",
                    "summary": "A licensed reviewer should verify the listed policy facts.",
                },
                evidence=({"type": "deterministic-test"},),
            ),
        )
    )
    notification = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="notification-worker:test",
            lease_duration=timedelta(minutes=5),
            kinds=("work_completed",),
        )
    )
    assert notification is not None

    status = await control_plane.resolve_notification_status(
        notification.notification_id,
        notification.workflow_event_id,
        notification.workflow_id,
        "notification-worker:test",
        notification.delivery_attempt,
    )

    assert status.message == (
        "Coverage review ready: A licensed reviewer should verify the listed policy facts."
    )
    snapshot = await service.snapshot()
    assert snapshot.notifications[0].kind == "work_completed"
    assert snapshot.approval_requests == ()

    await database.dispose()
    await service.dispose()


async def test_snapshot_bounds_repeated_demo_history(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    service = _service(migrated_postgres_url)

    await service.enqueue_workflows(20, "renewal")
    await service.enqueue_workflows(20, "renewal")
    snapshot = await service.enqueue_workflows(20, "renewal")

    assert snapshot.counts.workflows == 60
    assert snapshot.counts.jobs == 120
    assert snapshot.scope.visible_workflows == 50
    assert snapshot.scope.total_workflows == 60
    assert snapshot.scope.workflow_limit == 50
    assert snapshot.scope.truncated is True
    assert len(snapshot.jobs) == 100
    assert len(snapshot.activity) <= 80

    await service.dispose()


async def test_snapshot_prioritizes_active_work_outside_the_newest_workflow_window(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    service = _service(migrated_postgres_url)
    await service.enqueue_workflows(20, "renewal")
    await service.enqueue_workflows(20, "renewal")
    await service.enqueue_workflows(20, "renewal")
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )

    oldest = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="workflow-worker:oldest",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("renewal_email_drafter",),
        )
    )
    assert oldest is not None

    snapshot = await service.snapshot()

    assert snapshot.scope.visible_workflows == 50
    assert snapshot.scope.total_workflows == 60
    assert snapshot.counts.workflows == 60
    assert snapshot.counts.running == 1
    assert any(run.id == oldest.run_id and run.status == "running" for run in snapshot.runs)
    assert any(job.id == oldest.job_id and job.status == "running" for job in snapshot.jobs)

    await database.dispose()
    await service.dispose()


async def test_snapshot_prioritizes_delivering_notification_outside_newest_window(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    service = _service(migrated_postgres_url)
    await service.enqueue_workflows(20, "renewal")
    await service.enqueue_workflows(20, "renewal")
    await service.enqueue_workflows(20, "renewal")
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    draft = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="workflow-worker:oldest",
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
            worker_id="notification-worker:oldest",
            lease_duration=timedelta(minutes=5),
            kinds=("approval_required",),
        )
    )
    assert notification is not None

    snapshot = await service.snapshot()

    assert snapshot.counts.notifications_delivering == 1
    assert any(
        item.id == notification.notification_id and item.status == "delivering"
        for item in snapshot.notifications
    )

    await database.dispose()
    await service.dispose()
