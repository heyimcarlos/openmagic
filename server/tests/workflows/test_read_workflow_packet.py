from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.tests.workflows.retrieval_fixtures import (
    BROKER_ID,
    TARGET_ID,
    renewal_job_command,
    seed_retrieval_landscape,
)
from server.workflows import (
    StaticWorkflowAuthority,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowInspectionContext,
    WorkflowNotFoundError,
    WorkflowRetrieval,
    WorkflowSearchRequest,
    default_workflow_registry,
)
from server.workflows.identity_models import (
    PartyRow,
    WorkflowParticipantRoleRow,
    WorkflowParticipantRow,
)
from server.workflows.models import WorkflowEventRow, WorkflowJobRow, WorkflowJobRunRow


@pytest.fixture
async def retrieval(migrated_postgres_url: str, clean_workflow_database):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    yield WorkflowRetrieval(database=database, cursor_secret=b"issue-18-test-secret")
    await database.dispose()


async def test_packet_is_bounded_authorized_operational_context(retrieval: WorkflowRetrieval):
    packet = await retrieval.read_workflow_packet(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        TARGET_ID,
    )

    assert packet.packet_version == "v1"
    assert packet.workflow.workflow_id == TARGET_ID
    assert packet.workflow.organization == "Acme Brokerage"
    assert {participant.name for participant in packet.participants} == {
        "Carlos Broker",
        "John Smith",
    }
    assert packet.jobs == ()
    assert len(packet.recent_events) == 1
    assert packet.recent_events[0].summary == "Workflow created"
    assert packet.event_window.model_dump() == {
        "returned": 1,
        "total": 1,
        "has_earlier": False,
    }


async def test_packet_hides_nonexistent_and_unauthorized_workflows_identically(
    retrieval: WorkflowRetrieval,
):
    unauthorized = WorkflowInspectionContext(actor_party_id=uuid4())

    with pytest.raises(WorkflowNotFoundError):
        await retrieval.read_workflow_packet(unauthorized, TARGET_ID)
    with pytest.raises(WorkflowNotFoundError):
        await retrieval.read_workflow_packet(unauthorized, uuid4())


async def test_packet_projects_graph_and_latest_run_without_raw_execution_data(
    retrieval: WorkflowRetrieval,
    migrated_postgres_url: str,
):
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    trace = await control_plane.propose_jobs(renewal_job_command())
    draft_job = next(job for job in trace.jobs if job.kind == "renewal_email.draft.v1")
    run_id = uuid4()
    now = datetime.now(UTC)
    engine = create_async_engine(migrated_postgres_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        await session.execute(
            sa.update(WorkflowJobRow)
            .where(WorkflowJobRow.id == draft_job.id)
            .values(status="queued", attempts=1)
        )
        session.add(
            WorkflowJobRunRow(
                id=run_id,
                workflow_id=TARGET_ID,
                job_id=draft_job.id,
                status="failed",
                worker_id="secret-worker-host",
                lease_expires_at=now + timedelta(minutes=1),
                runtime_instance_id=uuid4(),
                application_build="secret-build-sha",
                adapter_version="secret-adapter-version",
                provider_tool_version="secret-provider-version",
                result={
                    "outcome": "failed",
                    "error": {
                        "message": "Bearer secret-token and private email body",
                    },
                },
                finished_at=now,
            )
        )
        await session.flush()
        session.add(
            WorkflowEventRow(
                workflow_id=TARGET_ID,
                job_id=draft_job.id,
                run_id=run_id,
                event_type="run_started",
                actor_type="worker",
                actor_id="secret-worker-host",
                cause_type="fixture",
                cause_id="run-fixture",
                data={"provider_payload": "must-not-appear"},
            )
        )
    await engine.dispose()

    packet = await retrieval.read_workflow_packet(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        TARGET_ID,
    )

    assert len(packet.jobs) == 2
    draft = next(job for job in packet.jobs if job.job_id == draft_job.id)
    send = next(job for job in packet.jobs if job.kind == "gmail.send_email.v1")
    assert draft.latest_run is not None
    assert draft.latest_run.run_id == run_id
    assert draft.latest_run.status == "failed"
    assert draft.latest_run.error_summary == "Run failed"
    assert send.depends_on_job_ids == (draft_job.id,)
    assert send.waiting_reasons == (f"dependency:{draft_job.id}",)
    encoded = packet.model_dump_json()
    for hidden in (
        "secret-worker-host",
        "secret-build-sha",
        "secret-adapter-version",
        "secret-provider-version",
        "provider_payload",
        "must-not-appear",
        "secret-token",
        "private email body",
    ):
        assert hidden not in encoded

    await database.dispose()


async def test_packet_and_search_revalidate_revoked_authority(
    retrieval: WorkflowRetrieval,
    migrated_postgres_url: str,
):
    context = WorkflowInspectionContext(actor_party_id=BROKER_ID)
    assert (
        await retrieval.read_workflow_packet(context, TARGET_ID)
    ).workflow.workflow_id == TARGET_ID

    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.update(WorkflowParticipantRoleRow)
            .where(
                WorkflowParticipantRoleRow.workflow_id == TARGET_ID,
                WorkflowParticipantRoleRow.party_id == BROKER_ID,
                WorkflowParticipantRoleRow.role == "Broker",
            )
            .values(revoked_at=datetime.now(UTC))
        )
    await engine.dispose()

    with pytest.raises(WorkflowNotFoundError):
        await retrieval.read_workflow_packet(context, TARGET_ID)
    page = await retrieval.search_workflows(
        context,
        # A direct identifier query must not recover the revoked Workflow.
        WorkflowSearchRequest(query=str(TARGET_ID)),
    )
    assert page.results == ()
    assert page.total_matches == 0


async def test_packet_returns_only_the_latest_twenty_interaction_events(
    retrieval: WorkflowRetrieval,
    migrated_postgres_url: str,
):
    engine = create_async_engine(migrated_postgres_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add_all(
            [
                WorkflowEventRow(
                    workflow_id=TARGET_ID,
                    event_type=f"interaction_fixture_{index:02d}",
                    actor_type="party",
                    actor_id=str(BROKER_ID),
                    cause_type="fixture",
                    cause_id=f"event-{index:02d}",
                    data={"raw": f"hidden-{index:02d}"},
                    occurred_at=now + timedelta(seconds=index),
                )
                for index in range(25)
            ]
        )
    await engine.dispose()

    packet = await retrieval.read_workflow_packet(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        TARGET_ID,
    )

    assert len(packet.recent_events) == 20
    assert packet.event_window.model_dump() == {
        "returned": 20,
        "total": 26,
        "has_earlier": True,
    }
    assert packet.recent_events[0].event_type == "interaction_fixture_05"
    assert packet.recent_events[-1].event_type == "interaction_fixture_24"
    assert "hidden-24" not in packet.model_dump_json()


async def test_packet_keeps_participants_without_a_current_role(
    retrieval: WorkflowRetrieval,
    migrated_postgres_url: str,
):
    roleless_party_id = uuid4()
    engine = create_async_engine(migrated_postgres_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        session.add(
            PartyRow(
                id=roleless_party_id,
                kind="person",
                display_name="Role Pending Participant",
            )
        )
        await session.flush()
        session.add(
            WorkflowParticipantRow(
                workflow_id=TARGET_ID,
                party_id=roleless_party_id,
            )
        )
    await engine.dispose()

    packet = await retrieval.read_workflow_packet(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        TARGET_ID,
    )

    participant = next(
        participant
        for participant in packet.participants
        if participant.party_id == roleless_party_id
    )
    assert participant.roles == ()
