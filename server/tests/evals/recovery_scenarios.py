"""Public-boundary journeys for the deterministic Workflow recovery suite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.agents.interaction_agent import InteractionToolContext, WorkflowInteractionToolbox
from server.evals import RecoveryCaseEvidence, build_recovery_case
from server.tests.workflows.factories import BROKER_ID, ORGANIZATION_ID, create_command
from server.tests.workflows.retrieval_fixtures import (
    renewal_job_command,
    seed_retrieval_landscape,
)
from server.workflows import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    RENEWAL_OUTREACH_KIND,
    ApproveWorkflowJobCommand,
    BeginExternalEffectDispatchCommand,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    DeterministicEmailSendAdapter,
    RecordInteractionCauseCommand,
    ReportRunResultCommand,
    RunResult,
    StaleRunError,
    StaticWorkflowAuthority,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowLifecycleError,
    WorkflowRetrieval,
    WorkflowWorker,
    default_workflow_registry,
)
from server.workflows.identity_models import (
    OrganizationMembershipRow,
    PartyIdentifierRow,
    PartyRow,
)
from server.workflows.models import InteractionCauseRow, WorkflowJobRunRow


async def run_recovery_scenarios(
    database_url: str,
    application_build: str,
) -> tuple[RecoveryCaseEvidence, ...]:
    """Run each recovery boundary from an isolated durable database state."""

    return (
        await _duplicate_cause_case(database_url),
        await _restart_awaiting_approval_case(database_url, application_build),
        await _worker_loss_before_dispatch_case(database_url, application_build),
        await _worker_loss_after_dispatch_case(database_url, application_build),
    )


async def _duplicate_cause_case(database_url: str) -> RecoveryCaseEvidence:
    await _reset_database(database_url)
    await seed_retrieval_landscape(database_url)
    database = WorkflowDatabase(database_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    command = renewal_job_command()
    await control_plane.record_interaction_cause(
        RecordInteractionCauseCommand(
            context=command.context,
            content="Prepare John Smith's 2026 renewal email at Acme Brokerage.",
        )
    )
    first = await control_plane.propose_jobs(command)
    replay = await control_plane.propose_jobs(command)
    assert replay == first
    draft, send = command.jobs
    conflicting_send = send.model_copy(
        update={"input": {**send.input, "cc": ["audit@example.com"]}}
    )
    conflict_rejected = False
    try:
        await control_plane.propose_jobs(
            command.model_copy(update={"jobs": (draft, conflicting_send)})
        )
    except WorkflowLifecycleError:
        conflict_rejected = True
    await database.dispose()
    return build_recovery_case(
        "duplicate-cause",
        replay,
        duplicate_deliveries=2,
        stable_replay_observed=replay == first,
        conflict_rejected=conflict_rejected,
    )


async def _restart_awaiting_approval_case(
    database_url: str,
    application_build: str,
) -> RecoveryCaseEvidence:
    await _reset_database(database_url)
    await _seed_workflow_identity(database_url)
    first_database, first_boundary = _new_control_plane(database_url)
    created, _presentation = await _present_send(first_boundary, application_build)
    workflow_id = created.workflow.id
    del created, _presentation, first_boundary
    await first_database.dispose()

    second_database, second_boundary = _new_control_plane(database_url)
    toolbox = WorkflowInteractionToolbox(
        retrieval=WorkflowRetrieval(
            database=second_database,
            cursor_secret=b"recovery-restart",
        ),
        control_plane=second_boundary,
    )
    interaction = InteractionToolContext(
        actor_party_id=BROKER_ID,
        organization_party_id=ORGANIZATION_ID,
        cause_id="restart-approval-message",
        trusted_workflow_id=workflow_id,
    )
    packet_result = await toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(workflow_id)},
        interaction,
    )
    assert packet_result.success is True
    packet = interaction.loaded_packet
    assert packet is not None
    draft = next(job for job in packet.jobs if job.kind == DRAFT_RENEWAL_EMAIL_KIND)
    send = next(job for job in packet.jobs if job.kind == GMAIL_SEND_EMAIL_KIND)
    assert draft.output is not None
    assert send.status == "waiting"
    assert tuple(reason.kind for reason in send.waiting_reasons) == ("exact_approval",)

    await toolbox.record_interaction_cause(interaction, "Yes, send this exact email.")
    approval = await toolbox.invoke(
        "approve_job",
        {
            "job_id": str(send.job_id),
            "expected_draft_revision_id": str(draft.job_id),
        },
        interaction,
    )
    assert approval.success is True
    trace = await second_boundary.read_workflow_trace(
        workflow_id,
        create_command().context,
    )
    await second_database.dispose()
    return build_recovery_case(
        "restart-awaiting-approval",
        trace,
        restart_boundaries=1,
    )


async def _worker_loss_before_dispatch_case(
    database_url: str,
    application_build: str,
) -> RecoveryCaseEvidence:
    await _reset_database(database_url)
    await _seed_workflow_identity(database_url)
    first_database, first_boundary = _new_control_plane(database_url)
    created = await first_boundary.create_workflow(create_command())
    first_run = await first_boundary.claim_job(_draft_claim("lost-draft-worker", application_build))
    assert first_run is not None
    await _expire_run(database_url, first_run.run_id)
    await first_database.dispose()

    second_database, second_boundary = _new_control_plane(database_url)
    second_run = await second_boundary.claim_job(
        _draft_claim("replacement-draft-worker", application_build)
    )
    assert second_run is not None
    stale_command_rejected = False
    try:
        await second_boundary.report_run_result(
            ReportRunResultCommand(run_id=first_run.run_id, result=_successful_draft())
        )
    except StaleRunError:
        stale_command_rejected = True
    else:
        raise AssertionError("abandoned Run retained execution authority")
    trace = await second_boundary.read_workflow_trace(
        created.workflow.id,
        create_command().context,
    )
    await second_database.dispose()
    return build_recovery_case(
        "worker-loss-before-dispatch",
        trace,
        restart_boundaries=1,
        stale_command_rejected=stale_command_rejected,
    )


async def _worker_loss_after_dispatch_case(
    database_url: str,
    application_build: str,
) -> RecoveryCaseEvidence:
    await _reset_database(database_url)
    await _seed_workflow_identity(database_url)
    first_database, first_boundary = _new_control_plane(database_url)
    created, presentation = await _present_send(first_boundary, application_build)
    approval_context = create_command().context.model_copy(
        update={"cause_id": "post-dispatch-approval"}
    )
    await first_boundary.record_interaction_cause(
        RecordInteractionCauseCommand(
            context=approval_context,
            content="Yes, send this exact email.",
        )
    )
    await first_boundary.approve_job(
        ApproveWorkflowJobCommand(
            context=approval_context,
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    first_run = await first_boundary.claim_job(_send_claim("lost-send-worker", application_build))
    assert first_run is not None
    dispatch = await first_boundary.begin_external_effect_dispatch(
        BeginExternalEffectDispatchCommand(run_id=first_run.run_id)
    )
    adapter = DeterministicEmailSendAdapter()
    lost_result = await adapter.send_email(dispatch.effect, dispatch.context)
    assert len(adapter.invocations) == 1
    await _expire_run(database_url, first_run.run_id)
    await first_database.dispose()

    second_database, second_boundary = _new_control_plane(database_url)
    worker = WorkflowWorker(
        control_plane=second_boundary,
        executors={},
        email_adapters={"composio_gmail_send": adapter},
        worker_id="replacement-send-worker",
        application_build=application_build,
    )
    assert await worker.run_once() is None
    assert len(adapter.invocations) == 1
    stale_command_rejected = False
    try:
        await second_boundary.report_run_result(
            ReportRunResultCommand(run_id=first_run.run_id, result=lost_result)
        )
    except StaleRunError:
        stale_command_rejected = True
    else:
        raise AssertionError("abandoned post-dispatch Run accepted a late result")
    trace = await second_boundary.read_workflow_trace(
        created.workflow.id,
        create_command().context,
    )
    await second_database.dispose()
    return build_recovery_case(
        "worker-loss-after-dispatch",
        trace,
        restart_boundaries=1,
        adapter_invocations=len(adapter.invocations),
        stale_command_rejected=stale_command_rejected,
    )


async def _present_send(
    control_plane: WorkflowControlPlane,
    application_build: str,
):
    created = await control_plane.create_workflow(create_command())
    draft_run = await control_plane.claim_job(_draft_claim("draft-worker", application_build))
    assert draft_run is not None
    await control_plane.report_run_result(
        ReportRunResultCommand(run_id=draft_run.run_id, result=_successful_draft())
    )
    notification = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="notification-worker",
            lease_duration=timedelta(minutes=5),
        )
    )
    assert notification is not None
    presentation = await control_plane.resolve_notification_presentation(
        notification.notification_id,
        notification.workflow_event_id,
        notification.workflow_id,
        "notification-worker",
        notification.delivery_attempt,
    )
    return created, presentation


def _new_control_plane(database_url: str) -> tuple[WorkflowDatabase, WorkflowControlPlane]:
    database = WorkflowDatabase(database_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(
            grants={(BROKER_ID, ORGANIZATION_ID, RENEWAL_OUTREACH_KIND)}
        ),
    )
    return database, control_plane


def _draft_claim(worker_id: str, application_build: str) -> ClaimWorkflowJobCommand:
    return ClaimWorkflowJobCommand(
        worker_id=worker_id,
        application_build=application_build,
        lease_duration=timedelta(minutes=5),
        executor_keys=("renewal_email_drafter",),
    )


def _send_claim(worker_id: str, application_build: str) -> ClaimWorkflowJobCommand:
    return ClaimWorkflowJobCommand(
        worker_id=worker_id,
        application_build=application_build,
        lease_duration=timedelta(minutes=5),
        executor_keys=("composio_gmail_send",),
    )


def _successful_draft() -> RunResult:
    return RunResult(
        outcome="succeeded",
        data={
            "subject": "Your 2026 policy renewal",
            "body": "Hello John Smith, your renewal is ready for review.",
        },
        evidence=({"type": "agent_output_validated"},),
    )


async def _expire_run(database_url: str, run_id) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowJobRunRow)
            .where(WorkflowJobRunRow.id == run_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    await engine.dispose()


async def _reset_database(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "TRUNCATE notifications, workflow_events, interaction_causes, "
                "workflow_job_runs, workflow_job_dependencies, workflow_jobs, "
                "workflow_participant_roles, workflow_participants, "
                "organization_memberships, party_identifiers, workflows, parties CASCADE"
            )
        )
    await engine.dispose()


async def _seed_workflow_identity(database_url: str) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add_all(
            [
                PartyRow(id=BROKER_ID, kind="person", display_name="Recovery Broker"),
                PartyRow(
                    id=ORGANIZATION_ID,
                    kind="organization",
                    display_name="Acme Brokerage",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                PartyIdentifierRow(
                    party_id=BROKER_ID,
                    kind="email",
                    value="broker@acme.example",
                    verified_at=now,
                ),
                OrganizationMembershipRow(
                    person_party_id=BROKER_ID,
                    organization_party_id=ORGANIZATION_ID,
                    granted_at=now,
                ),
                InteractionCauseRow(
                    id="message-renewal-request",
                    cause_type="message",
                    actor_party_id=BROKER_ID,
                    content_digest="recovery-fixture-cause",
                ),
            ]
        )
    await engine.dispose()


__all__ = ["run_recovery_scenarios"]
