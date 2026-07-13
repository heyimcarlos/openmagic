from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from server.tests.workflows.factories import BROKER_ID, create_command
from server.workflows import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    RENEWAL_OUTREACH_KIND,
    ClaimWorkflowJobCommand,
    ReportRunResultCommand,
    RunResult,
    WorkflowControlPlane,
    WorkflowLifecycleError,
)


async def test_creates_atomic_renewal_workflow_graph(control_plane: WorkflowControlPlane):
    trace = await control_plane.create_workflow(create_command())

    assert trace.workflow.kind == RENEWAL_OUTREACH_KIND
    assert trace.workflow.status == "active"
    assert trace.workflow.objective == "2026 renewal outreach for John Smith"
    assert trace.workflow.input == {"renewal_period": "2026"}

    jobs_by_kind = {job.kind: job for job in trace.jobs}
    draft = jobs_by_kind[DRAFT_RENEWAL_EMAIL_KIND]
    send = jobs_by_kind[GMAIL_SEND_EMAIL_KIND]

    assert draft.status == "queued"
    assert draft.attempts == 0
    assert draft.max_attempts == 2
    assert draft.output is None
    assert draft.waiting_reasons == ()

    assert send.status == "waiting"
    assert send.attempts == 0
    assert send.max_attempts == 3
    assert send.output is None
    assert send.depends_on_job_ids == (draft.id,)
    assert send.waiting_reasons == (f"dependency:{draft.id}",)
    assert send.input["subject"] == {"job_output": str(draft.id), "field": "subject"}
    assert send.input["body"] == {"job_output": str(draft.id), "field": "body"}

    assert len(trace.events) == 1
    event = trace.events[0]
    assert event.event_type == "workflow_jobs_proposed"
    assert event.actor_type == "party"
    assert event.actor_id == str(BROKER_ID)
    assert event.cause_type == "message"
    assert event.cause_id == "message-renewal-request"
    assert set(event.data["job_ids"]) == {str(draft.id), str(send.id)}

    assert trace.runs == ()
    assert trace.notifications == ()
    assert (
        await control_plane.read_workflow_trace(trace.workflow.id, create_command().context)
        == trace
    )


async def test_create_workflow_replays_stable_receipt_after_lifecycle_progress(
    control_plane: WorkflowControlPlane,
):
    command = create_command()
    first = await control_plane.create_workflow(command)
    claimed = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="create-replay-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("renewal_email_drafter",),
        )
    )
    assert claimed is not None
    await control_plane.report_run_result(
        ReportRunResultCommand(
            run_id=claimed.run_id,
            result=RunResult(
                outcome="failed",
                error={"code": "invalid_draft_output"},
            ),
        )
    )

    replay = await control_plane.create_workflow(command)

    assert replay == first


async def test_concurrent_create_delivery_replays_one_workflow(
    control_plane: WorkflowControlPlane,
):
    command = create_command()

    first, replay = await asyncio.gather(
        control_plane.create_workflow(command),
        control_plane.create_workflow(command),
    )

    assert replay == first
    assert replay.workflow.id == first.workflow.id


async def test_create_cause_rejects_a_conflicting_typed_workflow(
    control_plane: WorkflowControlPlane,
):
    command = create_command()
    accepted = await control_plane.create_workflow(command)
    conflicting = command.model_copy(
        update={
            "proposal": command.proposal.model_copy(
                update={"objective": "A conflicting renewal objective"}
            )
        }
    )

    with pytest.raises(WorkflowLifecycleError, match="Cause was already used"):
        await control_plane.create_workflow(conflicting)

    assert await control_plane.create_workflow(command) == accepted
