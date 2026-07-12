from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.agents.interaction_agent.toolbox import InteractionToolContext
from server.agents.interaction_agent.workflow_notifications import (
    FreshWorkflowInteractionFactory,
)
from server.agents.interaction_agent.workflow_tools import WorkflowInteractionToolbox
from server.config import Settings
from server.migrations.versions.a734d2a724bb_record_approval_causes import (
    SEND_ATTEMPT_UPGRADE_SQL,
)
from server.tests.workflows.factories import create_command
from server.workflows import (
    ApproveWorkflowJobCommand,
    BeginExternalEffectDispatchCommand,
    CancelWorkflowCommand,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    ComposioGmailSendAdapter,
    ComposioMailboxBinding,
    DeterministicEmailSendAdapter,
    DuplicateEmailSendError,
    EmailSendEffectV1,
    EmailSendExecutionContextV1,
    NotificationWorker,
    RecordInteractionCauseCommand,
    ReportRunResultCommand,
    RevokeWorkflowAuthorityCommand,
    RunResult,
    RunResultConflictError,
    WorkflowAuthorizationError,
    WorkflowCommandContext,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowLifecycleError,
    WorkflowRetrieval,
    WorkflowWorker,
)
from server.workflows.identity_models import OrganizationMembershipRow, WorkflowParticipantRoleRow
from server.workflows.models import WorkflowJobRow


def draft_claim() -> ClaimWorkflowJobCommand:
    return ClaimWorkflowJobCommand(
        worker_id="draft-worker",
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


async def presented_send(control_plane: WorkflowControlPlane):
    created = await control_plane.create_workflow(create_command())
    draft_run = await control_plane.claim_job(draft_claim())
    assert draft_run is not None
    await control_plane.report_run_result(
        ReportRunResultCommand(run_id=draft_run.run_id, result=successful_draft())
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
    await record_cause(control_plane, "approval-message-1")
    return created, presentation


async def record_cause(
    control_plane: WorkflowControlPlane,
    cause_id: str,
    content: str = "Yes, send this exact email",
    *,
    context: WorkflowCommandContext | None = None,
) -> None:
    await control_plane.record_interaction_cause(
        RecordInteractionCauseCommand(
            context=(context or create_command().context).model_copy(update={"cause_id": cause_id}),
            content=content,
        )
    )


async def test_exact_presented_send_approval_is_idempotent(
    control_plane: WorkflowControlPlane,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    command = ApproveWorkflowJobCommand(
        context=WorkflowCommandContext(
            actor_party_id=broker.actor_party_id,
            organization_party_id=broker.organization_party_id,
            cause_type="message",
            cause_id="approval-message-1",
        ),
        job_id=presentation.send_job_id,
        expected_draft_revision_id=presentation.draft_job_id,
    )

    first = await control_plane.approve_job(command)
    replay = await control_plane.approve_job(command)

    assert replay == first
    assert first.job_id == presentation.send_job_id
    assert first.draft_job_id == presentation.draft_job_id
    assert first.effect_fingerprint == presentation.effect_fingerprint
    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    send = next(job for job in trace.jobs if job.id == presentation.send_job_id)
    assert send.status == "queued"
    approvals = [event for event in trace.events if event.event_type == "approval_granted"]
    assert len(approvals) == 1
    assert approvals[0].actor_id == str(broker.actor_party_id)
    assert approvals[0].cause_type == "message"
    assert approvals[0].cause_id == "approval-message-1"
    with pytest.raises(WorkflowLifecycleError, match="Cause was already used"):
        await control_plane.approve_job(
            command.model_copy(update={"expected_draft_revision_id": created.workflow.id})
        )

    _second, second_presentation = await presented_send(control_plane)
    with pytest.raises(WorkflowLifecycleError, match="Cause was already used"):
        await control_plane.approve_job(
            ApproveWorkflowJobCommand(
                context=command.context,
                job_id=second_presentation.send_job_id,
                expected_draft_revision_id=second_presentation.draft_job_id,
            )
        )


async def test_one_cause_cannot_concurrently_approve_two_jobs(
    control_plane: WorkflowControlPlane,
):
    _first, first_presentation = await presented_send(control_plane)
    _second, second_presentation = await presented_send(control_plane)
    broker = create_command().context

    results = await asyncio.gather(
        *[
            control_plane.approve_job(
                ApproveWorkflowJobCommand(
                    context=broker.model_copy(update={"cause_id": "approval-message-1"}),
                    job_id=presentation.send_job_id,
                    expected_draft_revision_id=presentation.draft_job_id,
                )
            )
            for presentation in (first_presentation, second_presentation)
        ],
        return_exceptions=True,
    )

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    conflicts = [result for result in results if isinstance(result, BaseException)]
    assert len(conflicts) == 1
    assert isinstance(conflicts[0], WorkflowLifecycleError)
    assert "Cause was already used" in str(conflicts[0])


async def test_stale_or_unauthorized_approval_changes_nothing(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await record_cause(control_plane, "wrong-revision")
    with pytest.raises(WorkflowLifecycleError, match="Draft Revision is stale"):
        await control_plane.approve_job(
            ApproveWorkflowJobCommand(
                context=broker.model_copy(update={"cause_id": "wrong-revision"}),
                job_id=presentation.send_job_id,
                expected_draft_revision_id=created.workflow.id,
            )
        )
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowParticipantRoleRow)
            .where(
                WorkflowParticipantRoleRow.workflow_id == created.workflow.id,
                WorkflowParticipantRoleRow.party_id == broker.actor_party_id,
                WorkflowParticipantRoleRow.role == "Broker",
            )
            .values(revoked_at=datetime.now(UTC))
        )
    await engine.dispose()
    await record_cause(control_plane, "revoked-approval")
    with pytest.raises(WorkflowAuthorizationError):
        await control_plane.approve_job(
            ApproveWorkflowJobCommand(
                context=broker.model_copy(update={"cause_id": "revoked-approval"}),
                job_id=presentation.send_job_id,
                expected_draft_revision_id=presentation.draft_job_id,
            )
        )
    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    assert not any(event.event_type == "approval_granted" for event in trace.events)


async def test_approval_cause_must_follow_presentation_and_match_party(
    control_plane: WorkflowControlPlane,
):
    broker = create_command().context
    await record_cause(control_plane, "early-message", "Do not send this email")
    with pytest.raises(WorkflowLifecycleError, match="identity conflicts"):
        await record_cause(control_plane, "early-message", "Yes, send this exact email")
    _created, presentation = await presented_send(control_plane)

    with pytest.raises(WorkflowLifecycleError, match="predates the exact presentation"):
        await control_plane.approve_job(
            ApproveWorkflowJobCommand(
                context=broker.model_copy(update={"cause_id": "early-message"}),
                job_id=presentation.send_job_id,
                expected_draft_revision_id=presentation.draft_job_id,
            )
        )

    foreign_context = broker.model_copy(
        update={
            "actor_party_id": broker.organization_party_id,
            "cause_id": "wrong-author-message",
        }
    )
    await record_cause(
        control_plane,
        "wrong-author-message",
        context=foreign_context,
    )
    with pytest.raises(WorkflowLifecycleError, match="not authenticated"):
        await control_plane.approve_job(
            ApproveWorkflowJobCommand(
                context=broker.model_copy(update={"cause_id": "wrong-author-message"}),
                job_id=presentation.send_job_id,
                expected_draft_revision_id=presentation.draft_job_id,
            )
        )


async def test_dispatch_consumes_exact_approval_before_provider_call(
    control_plane: WorkflowControlPlane,
):
    _created, presentation = await presented_send(control_plane)
    broker = create_command().context
    grant = await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=WorkflowCommandContext(
                actor_party_id=broker.actor_party_id,
                organization_party_id=broker.organization_party_id,
                cause_type="message",
                cause_id="approval-message-1",
            ),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert run is not None

    dispatch = await control_plane.begin_external_effect_dispatch(
        BeginExternalEffectDispatchCommand(run_id=run.run_id)
    )

    assert dispatch.approval_grant_id == grant.approval_grant_id
    assert dispatch.effect_fingerprint == presentation.effect_fingerprint
    assert dispatch.effect.expected_sender_address == "broker@acme.example"
    assert dispatch.effect.to == ("john@example.com",)
    assert dispatch.effect.subject == "Your 2026 policy renewal"
    assert dispatch.context.job_id == presentation.send_job_id
    assert dispatch.context.run_id == run.run_id
    with pytest.raises(WorkflowLifecycleError, match="already started"):
        await control_plane.begin_external_effect_dispatch(
            BeginExternalEffectDispatchCommand(run_id=run.run_id)
        )


async def test_deterministic_adapter_records_one_exact_effect(
    control_plane: WorkflowControlPlane,
):
    _created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert run is not None
    dispatch = await control_plane.begin_external_effect_dispatch(
        BeginExternalEffectDispatchCommand(run_id=run.run_id)
    )
    adapter = DeterministicEmailSendAdapter()

    result = await adapter.send_email(dispatch.effect, dispatch.context)

    assert result.outcome == "succeeded"
    assert adapter.invocations == ((dispatch.effect, dispatch.context),)
    with pytest.raises(DuplicateEmailSendError):
        await adapter.send_email(dispatch.effect, dispatch.context)


class _Tools:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FailingTools:
    def execute(self, **_kwargs):
        raise TimeoutError("response lost")


async def test_composio_adapter_uses_pinned_public_execute_and_normalizes_success(
    control_plane: WorkflowControlPlane,
):
    _created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert run is not None
    dispatch = await control_plane.begin_external_effect_dispatch(
        BeginExternalEffectDispatchCommand(run_id=run.run_id)
    )
    tools = _Tools(
        {
            "successful": True,
            "error": None,
            "data": {"id": "gmail-message-1", "threadId": "gmail-thread-1"},
        }
    )
    adapter = ComposioGmailSendAdapter(
        client=SimpleNamespace(tools=tools),
        binding=ComposioMailboxBinding(
            sender_mailbox_id=dispatch.effect.sender_mailbox_id,
            expected_sender_address=dispatch.effect.expected_sender_address,
            composio_user_id="broker-connection",
        ),
    )

    result = await adapter.send_email(dispatch.effect, dispatch.context)

    assert result.outcome == "succeeded"
    assert adapter.invocation_count == 1
    assert result.data == {
        "provider": "composio_gmail",
        "acknowledged": True,
        "tool_version": "20260702_01",
        "message_id": "gmail-message-1",
        "thread_id": "gmail-thread-1",
    }
    assert tools.calls == [
        {
            "slug": "GMAIL_SEND_EMAIL",
            "user_id": "broker-connection",
            "version": "20260702_01",
            "arguments": {
                "user_id": "me",
                "recipient_email": "john@example.com",
                "extra_recipients": [],
                "cc": [],
                "bcc": [],
                "subject": "Your 2026 policy renewal",
                "body": "Hello John Smith,\n\nLet's review your 2026 renewal options.",
                "is_html": False,
            },
        }
    ]


@pytest.mark.parametrize(
    "response",
    [
        {"successful": False, "error": "provider error", "data": {}},
        {"successful": True, "error": "contradiction", "data": {}},
        {"successful": True, "error": None, "data": "malformed"},
        None,
    ],
)
async def test_composio_ambiguous_observations_are_uncertain(response):
    mailbox_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    effect = EmailSendEffectV1(
        sender_mailbox_id=mailbox_id,
        expected_sender_address="broker@example.com",
        to=("recipient@example.com",),
        subject="Correlation subject",
        body="Exact approved body",
    )
    context = EmailSendExecutionContextV1(
        job_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        run_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        effect_fingerprint="fingerprint",
    )
    adapter = ComposioGmailSendAdapter(
        client=SimpleNamespace(tools=_Tools(response)),
        binding=ComposioMailboxBinding(
            sender_mailbox_id=mailbox_id,
            expected_sender_address="broker@example.com",
            composio_user_id="broker-connection",
        ),
    )

    result = await adapter.send_email(effect, context)

    assert result.outcome == "uncertain"


async def test_composio_transport_loss_after_dispatch_is_uncertain():
    mailbox_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    effect = EmailSendEffectV1(
        sender_mailbox_id=mailbox_id,
        expected_sender_address="broker@example.com",
        to=("recipient@example.com",),
        subject="Correlation subject",
        body="Exact approved body",
    )
    adapter = ComposioGmailSendAdapter(
        client=SimpleNamespace(tools=_FailingTools()),
        binding=ComposioMailboxBinding(
            sender_mailbox_id=mailbox_id,
            expected_sender_address="broker@example.com",
            composio_user_id="broker-connection",
        ),
    )

    result = await adapter.send_email(
        effect,
        EmailSendExecutionContextV1(
            job_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            run_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            effect_fingerprint="fingerprint",
        ),
    )

    assert result.outcome == "uncertain"


async def test_worker_sends_once_and_completes_workflow_atomically(
    control_plane: WorkflowControlPlane,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    adapter = DeterministicEmailSendAdapter()
    worker = WorkflowWorker(
        control_plane=control_plane,
        executors={},
        email_adapters={"composio_gmail_send": adapter},
        worker_id="send-worker",
        application_build="test-build",
    )

    packet = await worker.run_once()

    assert packet is not None
    assert packet.job_id == presentation.send_job_id
    assert len(adapter.invocations) == 1
    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    send = next(job for job in trace.jobs if job.id == presentation.send_job_id)
    assert trace.workflow.status == "completed"
    assert send.status == "succeeded"
    assert send.output == {
        "provider": "composio_gmail",
        "acknowledged": True,
        "tool_version": "20260702_01",
        "message_id": None,
        "thread_id": None,
    }
    event_types = [event.event_type for event in trace.events]
    assert event_types.count("external_effect_dispatch_started") == 1
    assert event_types.count("email_send_succeeded") == 1
    assert event_types.count("workflow_completed") == 1
    assert [notification.kind for notification in trace.notifications] == [
        "approval_required",
        "send_confirmed",
    ]


async def test_uncertain_send_never_completes_or_requeues(
    control_plane: WorkflowControlPlane,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    adapter = DeterministicEmailSendAdapter(
        RunResult(
            outcome="uncertain",
            evidence=({"type": "provider_outcome_uncertain"},),
            error={"code": "provider_communication_lost"},
        )
    )
    worker = WorkflowWorker(
        control_plane=control_plane,
        executors={},
        email_adapters={"composio_gmail_send": adapter},
        worker_id="send-worker",
        application_build="test-build",
    )

    await worker.run_once()
    second = await worker.run_once()

    assert second is None
    assert len(adapter.invocations) == 1
    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    send = next(job for job in trace.jobs if job.id == presentation.send_job_id)
    assert trace.workflow.status == "active"
    assert send.status == "waiting"
    assert send.output is None
    await record_cause(control_plane, "approval-after-dispatch")
    with pytest.raises(WorkflowLifecycleError, match="already dispatched"):
        await control_plane.approve_job(
            ApproveWorkflowJobCommand(
                context=broker.model_copy(update={"cause_id": "approval-after-dispatch"}),
                job_id=presentation.send_job_id,
                expected_draft_revision_id=presentation.draft_job_id,
            )
        )


@pytest.mark.parametrize(
    ("adapter", "expected_job_status", "dispatch_count"),
    [
        (
            DeterministicEmailSendAdapter(pre_dispatch_error="mailbox binding missing"),
            "failed",
            0,
        ),
        (
            DeterministicEmailSendAdapter(invocation_error=TimeoutError("response lost")),
            "waiting",
            1,
        ),
    ],
)
async def test_fake_forces_pre_and_post_dispatch_failure_boundaries(
    control_plane: WorkflowControlPlane,
    adapter: DeterministicEmailSendAdapter,
    expected_job_status: str,
    dispatch_count: int,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    worker = WorkflowWorker(
        control_plane=control_plane,
        executors={},
        email_adapters={"composio_gmail_send": adapter},
        worker_id="send-worker",
        application_build="test-build",
    )

    await worker.run_once()

    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    send = next(job for job in trace.jobs if job.id == presentation.send_job_id)
    assert send.status == expected_job_status
    assert [event.event_type for event in trace.events].count(
        "external_effect_dispatch_started"
    ) == dispatch_count


async def test_send_result_replay_is_write_once_and_conflicts_fail(
    control_plane: WorkflowControlPlane,
):
    _created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert run is not None
    dispatch = await control_plane.begin_external_effect_dispatch(
        BeginExternalEffectDispatchCommand(run_id=run.run_id)
    )
    result = await DeterministicEmailSendAdapter().send_email(
        dispatch.effect,
        dispatch.context,
    )
    command = ReportRunResultCommand(run_id=run.run_id, result=result)

    first = await control_plane.report_run_result(command)
    replay = await control_plane.report_run_result(command)

    assert replay == first
    with pytest.raises(RunResultConflictError):
        await control_plane.report_run_result(
            ReportRunResultCommand(
                run_id=run.run_id,
                result=RunResult(
                    outcome="uncertain",
                    error={"code": "conflicting_delivery"},
                ),
            )
        )


async def test_send_result_requires_dispatch_and_post_dispatch_failure_never_retries(
    control_plane: WorkflowControlPlane,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert run is not None
    success = RunResult(
        outcome="succeeded",
        data={
            "provider": "composio_gmail",
            "acknowledged": True,
            "tool_version": "20260702_01",
        },
    )
    with pytest.raises(WorkflowLifecycleError, match="no committed dispatch"):
        await control_plane.report_run_result(
            ReportRunResultCommand(run_id=run.run_id, result=success)
        )

    await control_plane.begin_external_effect_dispatch(
        BeginExternalEffectDispatchCommand(run_id=run.run_id)
    )
    await control_plane.report_run_result(
        ReportRunResultCommand(
            run_id=run.run_id,
            result=RunResult(
                outcome="failed",
                error={"code": "provider_temporarily_unavailable"},
            ),
        )
    )

    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    send = next(job for job in trace.jobs if job.id == presentation.send_job_id)
    assert send.status == "waiting"
    assert send.attempts == 1


async def test_integrity_failure_is_audited_and_never_dispatched(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowJobRow)
            .where(WorkflowJobRow.id == presentation.draft_job_id)
            .values(
                output={
                    "subject": "Tampered subject",
                    "body": "Hello John Smith,\n\nLet's review your 2026 renewal options.",
                }
            )
        )
    await engine.dispose()
    run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert run is not None

    with pytest.raises(WorkflowLifecycleError, match="fingerprint does not match"):
        await control_plane.begin_external_effect_dispatch(
            BeginExternalEffectDispatchCommand(run_id=run.run_id)
        )
    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    assert [event.event_type for event in trace.events].count("effect_integrity_failed") == 1
    assert not any(event.event_type == "external_effect_dispatch_started" for event in trace.events)


async def test_cancellation_and_dispatch_have_one_transaction_winner(
    control_plane: WorkflowControlPlane,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    grant = await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert run is not None

    dispatch, cancellation = await asyncio.gather(
        control_plane.begin_external_effect_dispatch(
            BeginExternalEffectDispatchCommand(run_id=run.run_id)
        ),
        control_plane.cancel_workflow(
            CancelWorkflowCommand(
                context=broker.model_copy(update={"cause_id": "cancel-message-1"}),
                workflow_id=created.workflow.id,
            )
        ),
        return_exceptions=True,
    )

    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    dispatch_events = [
        event for event in trace.events if event.event_type == "external_effect_dispatch_started"
    ]
    invalidations = [event for event in trace.events if event.event_type == "approval_invalidated"]
    assert not isinstance(cancellation, BaseException)
    if isinstance(dispatch, WorkflowLifecycleError):
        assert cancellation.outcome == "cancelled"
        assert trace.workflow.status == "cancelled"
        assert dispatch_events == []
        assert len(invalidations) == 1
        assert invalidations[0].data["reason"] == "workflow_cancelled"
        assert invalidations[0].data["approval_grant_id"] == str(grant.approval_grant_id)
    else:
        assert cancellation.outcome == "too_late"
        assert trace.workflow.status == "active"
        assert len(dispatch_events) == 1
        assert invalidations == []


async def test_authority_revocation_and_dispatch_have_one_transaction_winner(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert run is not None

    dispatch, revocation = await asyncio.gather(
        control_plane.begin_external_effect_dispatch(
            BeginExternalEffectDispatchCommand(run_id=run.run_id)
        ),
        control_plane.revoke_authority(
            RevokeWorkflowAuthorityCommand(
                context=broker.model_copy(update={"cause_id": "revoke-role-message"}),
                workflow_id=created.workflow.id,
                subject_party_id=broker.actor_party_id,
                reason="broker_role_revoked",
            )
        ),
        return_exceptions=True,
    )

    assert not isinstance(revocation, BaseException)
    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    invalidations = [event for event in trace.events if event.event_type == "approval_invalidated"]
    if isinstance(dispatch, WorkflowLifecycleError):
        assert revocation.invalidated_grants == 1
        assert len(invalidations) == 1
        assert invalidations[0].data["reason"] == "broker_role_revoked"
        send = next(job for job in trace.jobs if job.id == presentation.send_job_id)
        assert send.status == "waiting"
        send_run = next(item for item in trace.runs if item.id == run.run_id)
        assert send_run.status == "cancelled"

        engine = create_async_engine(migrated_postgres_url)
        async with engine.begin() as connection:
            await connection.execute(
                sa.update(WorkflowParticipantRoleRow)
                .where(
                    WorkflowParticipantRoleRow.workflow_id == created.workflow.id,
                    WorkflowParticipantRoleRow.party_id == broker.actor_party_id,
                    WorkflowParticipantRoleRow.role == "Broker",
                )
                .values(revoked_at=None)
            )
        await engine.dispose()
        with pytest.raises(WorkflowLifecycleError):
            await control_plane.begin_external_effect_dispatch(
                BeginExternalEffectDispatchCommand(run_id=run.run_id)
            )
    else:
        assert revocation.invalidated_grants == 0
        assert invalidations == []


async def test_reapproval_after_predispatch_revocation_can_claim_a_fresh_run(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    first_run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker-one",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert first_run is not None

    result = await control_plane.revoke_authority(
        RevokeWorkflowAuthorityCommand(
            context=broker.model_copy(update={"cause_id": "revoke-role-message"}),
            workflow_id=created.workflow.id,
            subject_party_id=broker.actor_party_id,
            reason="broker_role_revoked",
        )
    )
    assert result.invalidated_grants == 1

    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowParticipantRoleRow)
            .where(
                WorkflowParticipantRoleRow.workflow_id == created.workflow.id,
                WorkflowParticipantRoleRow.party_id == broker.actor_party_id,
                WorkflowParticipantRoleRow.role == "Broker",
            )
            .values(revoked_at=None)
        )
    await engine.dispose()
    await record_cause(control_plane, "replacement-approval-message")
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "replacement-approval-message"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )

    second_run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker-two",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert second_run is not None
    assert second_run.run_id != first_run.run_id
    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    send = next(job for job in trace.jobs if job.id == presentation.send_job_id)
    assert send.attempts == 2
    dispatch = await control_plane.begin_external_effect_dispatch(
        BeginExternalEffectDispatchCommand(run_id=second_run.run_id)
    )
    assert dispatch.context.run_id == second_run.run_id


async def test_membership_revocation_invalidates_every_affected_workflow(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    first, first_presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=first_presentation.send_job_id,
            expected_draft_revision_id=first_presentation.draft_job_id,
        )
    )
    second, second_presentation = await presented_send(control_plane)
    await record_cause(control_plane, "second-workflow-approval")
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "second-workflow-approval"}),
            job_id=second_presentation.send_job_id,
            expected_draft_revision_id=second_presentation.draft_job_id,
        )
    )

    result = await control_plane.revoke_authority(
        RevokeWorkflowAuthorityCommand(
            context=broker.model_copy(update={"cause_id": "revoke-membership-message"}),
            workflow_id=first.workflow.id,
            subject_party_id=broker.actor_party_id,
            reason="organization_membership_revoked",
        )
    )

    assert result.invalidated_grants == 2
    for created, presentation in (
        (first, first_presentation),
        (second, second_presentation),
    ):
        trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
        invalidations = [
            event for event in trace.events if event.event_type == "approval_invalidated"
        ]
        send = next(job for job in trace.jobs if job.id == presentation.send_job_id)
        assert len(invalidations) == 1
        assert invalidations[0].data["reason"] == "organization_membership_revoked"
        assert send.status == "waiting"

    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(OrganizationMembershipRow)
            .where(
                OrganizationMembershipRow.person_party_id == broker.actor_party_id,
                OrganizationMembershipRow.organization_party_id == broker.organization_party_id,
            )
            .values(revoked_at=None)
        )
    await engine.dispose()
    assert (
        await control_plane.claim_job(
            ClaimWorkflowJobCommand(
                worker_id="send-worker",
                application_build="test-build",
                lease_duration=timedelta(minutes=5),
                executor_keys=("composio_gmail_send",),
            )
        )
        is None
    )


async def test_predispatch_revocation_fails_an_exhausted_send_job(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowJobRow)
            .where(WorkflowJobRow.id == presentation.send_job_id)
            .values(max_attempts=1)
        )
    await engine.dispose()
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert run is not None

    await control_plane.revoke_authority(
        RevokeWorkflowAuthorityCommand(
            context=broker.model_copy(update={"cause_id": "revoke-role-message"}),
            workflow_id=created.workflow.id,
            subject_party_id=broker.actor_party_id,
            reason="broker_role_revoked",
        )
    )

    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    send = next(job for job in trace.jobs if job.id == presentation.send_job_id)
    assert send.status == "failed"


async def test_attempt_upgrade_includes_running_undispatched_send(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    _created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    run = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="send-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_gmail_send",),
        )
    )
    assert run is not None

    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowJobRow)
            .where(WorkflowJobRow.id == presentation.send_job_id)
            .values(max_attempts=1)
        )
        await connection.execute(SEND_ATTEMPT_UPGRADE_SQL)
        max_attempts = await connection.scalar(
            sa.select(WorkflowJobRow.max_attempts).where(
                WorkflowJobRow.id == presentation.send_job_id
            )
        )
    await engine.dispose()

    assert max_attempts == 3


async def test_interaction_tool_approves_only_loaded_exact_job(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    database = WorkflowDatabase(migrated_postgres_url)
    toolbox = WorkflowInteractionToolbox(
        retrieval=WorkflowRetrieval(database=database, cursor_secret=b"approval-tool"),
        control_plane=control_plane,
    )
    context = InteractionToolContext(
        actor_party_id=broker.actor_party_id,
        organization_party_id=broker.organization_party_id,
        cause_id="approval-message-tool",
        trusted_workflow_id=created.workflow.id,
    )
    await toolbox.record_interaction_cause(context, "I approve sending this exact email")
    packet = await toolbox.invoke(
        "read_workflow_packet",
        {"workflow_id": str(created.workflow.id)},
        context,
    )

    approved = await toolbox.invoke(
        "approve_job",
        {
            "job_id": str(presentation.send_job_id),
            "expected_draft_revision_id": str(presentation.draft_job_id),
        },
        context,
    )

    assert packet.success is True
    assert approved.success is True
    assert approved.payload["job_id"] == str(presentation.send_job_id)
    await database.dispose()


class _UnusedApprovalPresenter:
    async def present(
        self,
        notification_id: UUID,
        destination_party_id: UUID,
        effect: dict[str, object],
    ) -> str:
        del notification_id, destination_party_id, effect
        raise AssertionError("send confirmation must not use approval presentation")


class _ReplyLog:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def record_reply_once(self, _delivery_id: str, message: str) -> bool:
        self.messages.append(message)
        return True


async def test_send_confirmation_uses_fresh_packet_driven_interaction(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    created, presentation = await presented_send(control_plane)
    broker = create_command().context
    await control_plane.approve_job(
        ApproveWorkflowJobCommand(
            context=broker.model_copy(update={"cause_id": "approval-message-1"}),
            job_id=presentation.send_job_id,
            expected_draft_revision_id=presentation.draft_job_id,
        )
    )
    await WorkflowWorker(
        control_plane=control_plane,
        executors={},
        email_adapters={"composio_gmail_send": DeterministicEmailSendAdapter()},
        worker_id="send-worker",
        application_build="test-build",
    ).run_once()
    calls = 0

    async def fake_llm_call(self, _system_prompt, _messages):
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

    reply_log = _ReplyLog()
    monkeypatch.setattr(InteractionAgentRuntime, "_make_llm_call", fake_llm_call)
    monkeypatch.setattr(
        "server.agents.interaction_agent.workflow_notifications.get_conversation_log",
        lambda: reply_log,
    )
    database = WorkflowDatabase(migrated_postgres_url)
    notification_worker = NotificationWorker(
        control_plane=control_plane,
        interactions=FreshWorkflowInteractionFactory(
            control_plane=control_plane,
            retrieval=WorkflowRetrieval(database=database, cursor_secret=b"send-confirmed"),
            presenter=_UnusedApprovalPresenter(),
            settings=Settings(openrouter_api_key="test-key"),
            organization_party_id=broker.organization_party_id,
        ),
        worker_id="confirmation-worker",
    )

    delivered = await notification_worker.run_once()

    assert delivered is not None
    assert reply_log.messages == ["The renewal email was sent successfully."]
    trace = await control_plane.read_workflow_trace(created.workflow.id, broker)
    send_notification = next(item for item in trace.notifications if item.kind == "send_confirmed")
    assert send_notification.status == "delivered"
    await database.dispose()
