from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from server.services.conversation import WorkflowTelemetryProjector
from server.tests.workflows.factories import BROKER_ID as PROTOCOL_BROKER_ID
from server.tests.workflows.retrieval_fixtures import (
    BROKER_ID,
    SAME_NAME_ID,
    TARGET_ID,
    UNAUTHORIZED_ID,
    renewal_job_command,
    seed_retrieval_landscape,
)
from server.workflows import (
    ClaimWorkflowJobCommand,
    InteractionActivityAction,
    InteractionActivityStatus,
    InteractionActivityStore,
    RecordInteractionCauseCommand,
    ReportRunResultCommand,
    RunResult,
    StaticWorkflowAuthority,
    WorkflowCommandContext,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowRetrieval,
    default_workflow_registry,
)
from server.workflows.models import WorkflowEventRow


def test_cancelled_send_has_no_available_approval_checkpoint():
    job = SimpleNamespace(status="cancelled", waiting_reasons=(), approval=None)

    assert WorkflowTelemetryProjector._approval_status(job) == "unavailable"


async def test_projects_sanitized_activity_and_current_authorized_workflow_state(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    trace = await control_plane.propose_jobs(renewal_job_command())
    jobs = {job.kind: job for job in trace.jobs}
    async with database.transaction() as session:
        session.add(
            WorkflowEventRow(
                workflow_id=UNAUTHORIZED_ID,
                event_type="workflow_jobs_proposed",
                actor_type="party",
                actor_id=str(BROKER_ID),
                cause_type="message",
                cause_id="renewal-request-message",
                data={},
            )
        )
    store = InteractionActivityStore(database)
    search = await store.start(
        cause_id="renewal-request-message",
        action=InteractionActivityAction.SEARCH_WORKFLOWS,
    )
    await store.finish(
        search.id,
        status=InteractionActivityStatus.SUCCEEDED,
        workflow_id=TARGET_ID,
    )
    projector = WorkflowTelemetryProjector(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"telemetry-test"),
        activity_store=store,
        registry=default_workflow_registry(),
    )

    projected = await projector.project(
        actor_party_id=BROKER_ID,
        cause_ids=["renewal-request-message"],
    )

    telemetry = projected["renewal-request-message"]
    assert telemetry.activity_summary == "Found context for 1 Workflow"
    assert [(item.label, item.status) for item in telemetry.activity] == [
        ("Searched authorized Workflows", "succeeded")
    ]
    assert len(telemetry.workflows) == 1
    workflow = telemetry.workflows[0]
    assert workflow.id == str(TARGET_ID)
    assert workflow.title == "John Smith renewal outreach"
    assert workflow.status_label == "In progress"
    assert [(stage.kind, stage.label, stage.status) for stage in workflow.stages] == [
        ("job", "Draft renewal email", "queued"),
        ("checkpoint", "Exact approval", "unavailable"),
        ("job", "Send approved email", "waiting"),
    ]

    draft = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="telemetry-draft-worker",
            application_build="telemetry-test",
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
                evidence=({"source": "deterministic-test"},),
            ),
        )
    )
    async with database.transaction() as session:
        session.add(
            WorkflowEventRow(
                workflow_id=TARGET_ID,
                job_id=jobs["gmail.send_email.v1"].id,
                event_type="approval_presentation_committed",
                actor_type="worker",
                actor_id="notification-worker",
                cause_type="notification",
                cause_id="approval-notification",
                data={
                    "draft_job_id": str(jobs["renewal_email.draft.v1"].id),
                    "effect_fingerprint": "test-fingerprint",
                },
            )
        )

    refreshed = await projector.project(
        actor_party_id=BROKER_ID,
        cause_ids=["notification:approval-notification"],
    )
    workflow = refreshed["notification:approval-notification"].workflows[0]
    assert workflow.status_label == "Waiting for approval"
    assert [(stage.kind, stage.status) for stage in workflow.stages] == [
        ("job", "succeeded"),
        ("checkpoint", "waiting"),
        ("job", "waiting"),
    ]
    approval = refreshed["notification:approval-notification"].approval_request
    assert approval is not None
    assert approval.job_id == str(jobs["gmail.send_email.v1"].id)
    assert approval.draft_revision_id == str(jobs["renewal_email.draft.v1"].id)
    assert approval.sender == "broker@acme.example"
    assert approval.to == ["john@example.com"]
    assert approval.subject == "2026 renewal"
    assert approval.body == "Hello John"
    cockpit = refreshed["notification:approval-notification"].cockpit
    assert cockpit is not None
    assert cockpit.workflow.id == str(TARGET_ID)
    assert [job.status for job in cockpit.jobs] == ["succeeded", "waiting"]
    assert cockpit.events[-1].type == "approval_presentation_committed"
    await database.dispose()


async def test_projector_returns_no_turn_for_causes_without_visible_work(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    database = WorkflowDatabase(migrated_postgres_url)
    projector = WorkflowTelemetryProjector(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"empty-telemetry"),
        activity_store=InteractionActivityStore(database),
        registry=default_workflow_registry(),
    )

    assert (
        await projector.project(
            actor_party_id=PROTOCOL_BROKER_ID,
            cause_ids=["message-renewal-request"],
        )
        == {}
    )
    await database.dispose()


async def test_running_activity_is_not_reported_as_completed(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    database = WorkflowDatabase(migrated_postgres_url)
    store = InteractionActivityStore(database)
    await store.start(
        cause_id="message-renewal-request",
        action=InteractionActivityAction.SEARCH_WORKFLOWS,
    )
    projector = WorkflowTelemetryProjector(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"running-telemetry"),
        activity_store=store,
        registry=default_workflow_registry(),
    )

    telemetry = (
        await projector.project(
            actor_party_id=PROTOCOL_BROKER_ID,
            cause_ids=["message-renewal-request"],
        )
    )["message-renewal-request"]

    assert telemetry.activity_summary == "1 Agent action in progress"
    assert telemetry.workflows == []
    await database.dispose()


async def test_multiple_workflows_follow_first_successful_receipt_order(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    cause_id = "multi-workflow-message"
    await control_plane.record_interaction_cause(
        RecordInteractionCauseCommand(
            context=WorkflowCommandContext(
                actor_party_id=BROKER_ID,
                organization_party_id=renewal_job_command().context.organization_party_id,
                cause_type="message",
                cause_id=cause_id,
            ),
            content="Compare both renewal Workflows.",
        )
    )
    store = InteractionActivityStore(database)
    for workflow_id in (SAME_NAME_ID, TARGET_ID):
        receipt = await store.start(
            cause_id=cause_id,
            action=InteractionActivityAction.READ_WORKFLOW_PACKET,
        )
        await store.finish(
            receipt.id,
            status=InteractionActivityStatus.SUCCEEDED,
            workflow_id=workflow_id,
        )
    projector = WorkflowTelemetryProjector(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"multi-telemetry"),
        activity_store=store,
        registry=default_workflow_registry(),
    )

    telemetry = (await projector.project(actor_party_id=BROKER_ID, cause_ids=[cause_id]))[cause_id]

    assert [workflow.id for workflow in telemetry.workflows] == [
        str(SAME_NAME_ID),
        str(TARGET_ID),
    ]
    await database.dispose()


async def test_sensitive_workflow_is_hidden_until_packet_read_succeeds(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    cause_id = "sensitive-read-message"
    await control_plane.record_interaction_cause(
        RecordInteractionCauseCommand(
            context=WorkflowCommandContext(
                actor_party_id=BROKER_ID,
                organization_party_id=renewal_job_command().context.organization_party_id,
                cause_type="message",
                cause_id=cause_id,
            ),
            content="Show me the renewal details.",
        )
    )
    async with database.transaction() as session:
        session.add(
            WorkflowEventRow(
                workflow_id=TARGET_ID,
                event_type="verification_challenge_created",
                actor_type="party",
                actor_id=str(BROKER_ID),
                cause_type="message",
                cause_id=cause_id,
                data={},
            )
        )
    store = InteractionActivityStore(database)
    search = await store.start(
        cause_id=cause_id,
        action=InteractionActivityAction.SEARCH_WORKFLOWS,
    )
    await store.finish(
        search.id,
        status=InteractionActivityStatus.SUCCEEDED,
        workflow_id=TARGET_ID,
    )
    failed_read = await store.start(
        cause_id=cause_id,
        action=InteractionActivityAction.READ_WORKFLOW_PACKET,
    )
    await store.finish(
        failed_read.id,
        status=InteractionActivityStatus.FAILED,
        workflow_id=TARGET_ID,
    )
    projector = WorkflowTelemetryProjector(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"step-up-telemetry"),
        activity_store=store,
        registry=default_workflow_registry(),
    )

    before_verification = (await projector.project(actor_party_id=BROKER_ID, cause_ids=[cause_id]))[
        cause_id
    ]
    assert before_verification.activity_summary == "Agent actions need attention"
    assert before_verification.workflows == []

    successful_read = await store.start(
        cause_id=cause_id,
        action=InteractionActivityAction.READ_WORKFLOW_PACKET,
    )
    await store.finish(
        successful_read.id,
        status=InteractionActivityStatus.SUCCEEDED,
        workflow_id=TARGET_ID,
    )
    after_verification = (await projector.project(actor_party_id=BROKER_ID, cause_ids=[cause_id]))[
        cause_id
    ]
    assert [workflow.id for workflow in after_verification.workflows] == [str(TARGET_ID)]
    await database.dispose()
