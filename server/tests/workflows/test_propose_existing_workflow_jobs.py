from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from server.tests.workflows.retrieval_fixtures import (
    OTHER_BROKER_ID,
    TARGET_ID,
    renewal_job_command,
    seed_retrieval_landscape,
)
from server.workflows import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    VERIFICATION_EMAIL_JOB_KIND,
    ClaimWorkflowJobCommand,
    InvalidWorkflowProposalError,
    RecordInteractionCauseCommand,
    StaticWorkflowAuthority,
    WorkflowAuthorizationError,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowJobProposal,
    WorkflowLifecycleError,
    default_workflow_registry,
)
from server.workflows.identity_models import WorkflowParticipantRoleRow


async def job_count(database_url: str) -> int:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        count = await connection.scalar(sa.text("SELECT count(*) FROM workflow_jobs"))
    await engine.dispose()
    assert count is not None
    return count


async def test_proposes_atomic_job_graph_against_selected_workflow(
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

    assert trace.workflow.id == TARGET_ID
    assert trace.workflow.status == "active"
    jobs = {job.kind: job for job in trace.jobs}
    draft = jobs[DRAFT_RENEWAL_EMAIL_KIND]
    send = jobs[GMAIL_SEND_EMAIL_KIND]
    assert draft.status == "queued"
    assert send.status == "waiting"
    assert send.depends_on_job_ids == (draft.id,)
    assert send.input["subject"] == {"job_output": str(draft.id), "field": "subject"}

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        workflow_count = await connection.scalar(sa.text("SELECT count(*) FROM workflows"))
    await engine.dispose()
    await database.dispose()
    assert workflow_count == 6


async def test_unauthorized_proposal_changes_nothing(
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
    command = renewal_job_command()
    unauthorized = command.model_copy(
        update={"context": command.context.model_copy(update={"actor_party_id": OTHER_BROKER_ID})}
    )

    with pytest.raises(WorkflowAuthorizationError):
        await control_plane.propose_jobs(unauthorized)

    assert await job_count(migrated_postgres_url) == 0
    await database.dispose()


async def test_invalid_graph_changes_nothing(
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
    command = renewal_job_command()

    with pytest.raises(InvalidWorkflowProposalError):
        await control_plane.propose_jobs(command.model_copy(update={"jobs": command.jobs[:1]}))

    assert await job_count(migrated_postgres_url) == 0
    await database.dispose()


async def test_party_cannot_propose_a_system_authorized_job(
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
    command = renewal_job_command().model_copy(
        update={
            "jobs": (
                WorkflowJobProposal(
                    key="verification",
                    kind=VERIFICATION_EMAIL_JOB_KIND,
                    input={"challenge_id": str(uuid4())},
                ),
            )
        }
    )

    with pytest.raises(WorkflowAuthorizationError, match="system-authorized"):
        await control_plane.propose_jobs(command)

    assert await job_count(migrated_postgres_url) == 0
    await database.dispose()


async def test_second_initial_graph_is_rejected_without_additional_jobs(
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
    command = renewal_job_command()
    await control_plane.propose_jobs(command)

    different_cause = command.model_copy(
        update={"context": command.context.model_copy(update={"cause_id": "another-message"})}
    )
    await control_plane.record_interaction_cause(
        RecordInteractionCauseCommand(
            context=different_cause.context,
            content="Create another initial graph.",
        )
    )
    with pytest.raises(WorkflowLifecycleError):
        await control_plane.propose_jobs(different_cause)

    assert await job_count(migrated_postgres_url) == 2
    await database.dispose()


async def test_authenticated_duplicate_cause_replays_one_stable_job_graph(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    base = renewal_job_command()
    command = base.model_copy(
        update={"context": base.context.model_copy(update={"cause_id": "duplicate-replay"})}
    )
    database = WorkflowDatabase(migrated_postgres_url)
    first_boundary = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    await first_boundary.record_interaction_cause(
        RecordInteractionCauseCommand(
            context=command.context,
            content="Prepare John Smith's 2026 renewal email at Acme Brokerage.",
        )
    )

    first = await first_boundary.propose_jobs(command)
    replay = await first_boundary.propose_jobs(command)

    assert replay == first
    assert len(replay.jobs) == 2
    assert [event.event_type for event in replay.events].count("workflow_jobs_proposed") == 1
    assert await first_boundary.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="replay-proof-worker",
            application_build="test-build",
            lease_duration=timedelta(minutes=5),
            executor_keys=("renewal_email_drafter",),
        )
    )
    assert await first_boundary.propose_jobs(command) == first
    assert await job_count(migrated_postgres_url) == 2
    await database.dispose()


async def test_concurrent_duplicate_cause_creates_one_job_graph(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    base = renewal_job_command()
    command = base.model_copy(
        update={"context": base.context.model_copy(update={"cause_id": "concurrent-replay"})}
    )
    first_database = WorkflowDatabase(migrated_postgres_url)
    second_database = WorkflowDatabase(migrated_postgres_url)
    first_boundary = WorkflowControlPlane(
        database=first_database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    second_boundary = WorkflowControlPlane(
        database=second_database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    cause = RecordInteractionCauseCommand(
        context=command.context,
        content="Prepare John Smith's 2026 renewal email at Acme Brokerage.",
    )
    await asyncio.gather(
        first_boundary.record_interaction_cause(cause),
        second_boundary.record_interaction_cause(cause),
    )

    first, replay = await asyncio.gather(
        first_boundary.propose_jobs(command),
        second_boundary.propose_jobs(command),
    )

    assert replay == first
    assert await job_count(migrated_postgres_url) == 2
    await first_database.dispose()
    await second_database.dispose()


async def test_duplicate_cause_with_conflicting_content_changes_nothing(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    base = renewal_job_command()
    command = base.model_copy(
        update={"context": base.context.model_copy(update={"cause_id": "content-conflict"})}
    )
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    await control_plane.record_interaction_cause(
        RecordInteractionCauseCommand(context=command.context, content="Prepare renewal email.")
    )

    with pytest.raises(WorkflowLifecycleError, match="Cause identity conflicts"):
        await control_plane.record_interaction_cause(
            RecordInteractionCauseCommand(context=command.context, content="Cancel renewal email.")
        )

    assert await job_count(migrated_postgres_url) == 0
    await database.dispose()


async def test_duplicate_cause_with_conflicting_typed_graph_changes_nothing(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    base = renewal_job_command()
    command = base.model_copy(
        update={"context": base.context.model_copy(update={"cause_id": "graph-conflict"})}
    )
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    await control_plane.record_interaction_cause(
        RecordInteractionCauseCommand(context=command.context, content="Prepare renewal email.")
    )
    accepted = await control_plane.propose_jobs(command)
    draft, send = command.jobs
    conflicting_send = send.model_copy(
        update={"input": {**send.input, "cc": ["audit@example.com"]}}
    )

    with pytest.raises(WorkflowLifecycleError, match="Cause was already used"):
        await control_plane.propose_jobs(
            command.model_copy(update={"jobs": (draft, conflicting_send)})
        )

    trace = await control_plane.propose_jobs(command)
    assert trace == accepted
    assert await job_count(migrated_postgres_url) == 2
    await database.dispose()


async def test_policyholder_revocation_between_packet_and_proposal_fails_closed(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowParticipantRoleRow)
            .where(
                WorkflowParticipantRoleRow.workflow_id == TARGET_ID,
                WorkflowParticipantRoleRow.role == "Policyholder",
            )
            .values(revoked_at=datetime.now(UTC))
        )
    await engine.dispose()
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )

    with pytest.raises(WorkflowLifecycleError):
        await control_plane.propose_jobs(renewal_job_command())

    assert await job_count(migrated_postgres_url) == 0
    await database.dispose()


async def test_unrelated_recipient_is_rejected_without_creating_jobs(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    command = renewal_job_command()
    draft, send = command.jobs
    unrelated_send = send.model_copy(
        update={"input": {**send.input, "to": ["unrelated@example.com"]}}
    )
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )

    with pytest.raises(WorkflowLifecycleError):
        await control_plane.propose_jobs(
            command.model_copy(update={"jobs": (draft, unrelated_send)})
        )

    assert await job_count(migrated_postgres_url) == 0
    await database.dispose()
