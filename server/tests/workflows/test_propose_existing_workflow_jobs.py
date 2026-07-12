from __future__ import annotations

from datetime import UTC, datetime

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
    InvalidWorkflowProposalError,
    StaticWorkflowAuthority,
    WorkflowAuthorizationError,
    WorkflowControlPlane,
    WorkflowDatabase,
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

    with pytest.raises(WorkflowLifecycleError):
        await control_plane.propose_jobs(command)

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
