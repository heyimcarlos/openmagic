from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from server.tests.workflows.factories import (
    BROKER_ID,
    ORGANIZATION_ID,
    create_command,
    renewal_proposal,
)
from server.workflows import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    RENEWAL_OUTREACH_KIND,
    ExecutionStrategy,
    InvalidWorkflowProposalError,
    StaticWorkflowAuthority,
    UnknownWorkflowJobKindError,
    UnknownWorkflowKindError,
    WorkflowAuthorizationError,
    WorkflowCommandContext,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowJobProposal,
    WorkflowKindContract,
    WorkflowKindRegistry,
    WorkflowNotFoundError,
    default_workflow_registry,
)


async def workflow_count(database_url: str) -> int:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        count = await connection.scalar(sa.text("SELECT count(*) FROM workflows"))
    await engine.dispose()
    assert count is not None
    return count


async def test_rejects_unknown_workflow_kind_without_mutation(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    proposal = renewal_proposal().model_copy(update={"kind": "unknown_workflow.v1"})

    with pytest.raises(UnknownWorkflowKindError):
        await control_plane.create_workflow(create_command(proposal))

    assert await workflow_count(migrated_postgres_url) == 0


async def test_rejects_unknown_job_kind_without_mutation(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    original = renewal_proposal()
    unknown_send = original.jobs[1].model_copy(update={"kind": "unknown_job.v1"})
    proposal = original.model_copy(update={"jobs": (original.jobs[0], unknown_send)})

    with pytest.raises(UnknownWorkflowJobKindError):
        await control_plane.create_workflow(create_command(proposal))

    assert await workflow_count(migrated_postgres_url) == 0


async def test_rejects_invalid_workflow_input_without_mutation(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    proposal = renewal_proposal().model_copy(update={"input": {"renewal_period": "FY26"}})

    with pytest.raises(InvalidWorkflowProposalError):
        await control_plane.create_workflow(create_command(proposal))

    assert await workflow_count(migrated_postgres_url) == 0


async def test_rejects_cyclic_graph_without_mutation(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    original = renewal_proposal()
    draft = original.jobs[0].model_copy(update={"depends_on": ("send",)})
    send = original.jobs[1].model_copy(update={"depends_on": ("draft",)})
    proposal = original.model_copy(update={"jobs": (draft, send)})

    with pytest.raises(InvalidWorkflowProposalError, match="cycle"):
        await control_plane.create_workflow(create_command(proposal))

    assert await workflow_count(migrated_postgres_url) == 0


async def test_rejects_unauthorized_creation_without_mutation(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )

    with pytest.raises(WorkflowAuthorizationError):
        await control_plane.create_workflow(create_command())

    assert await workflow_count(migrated_postgres_url) == 0
    await database.dispose()


async def test_rejects_unauthorized_trace_read(
    control_plane: WorkflowControlPlane,
):
    trace = await control_plane.create_workflow(create_command())
    unauthorized_context = WorkflowCommandContext(
        actor_party_id=uuid4(),
        organization_party_id=uuid4(),
        cause_type="message",
        cause_id="unauthorized-read",
    )

    with pytest.raises(WorkflowNotFoundError):
        await control_plane.read_workflow_trace(trace.workflow.id, unauthorized_context)


async def test_rejects_authorized_cross_organization_trace_read(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    other_broker_id = uuid4()
    other_organization_id = uuid4()
    database = WorkflowDatabase(migrated_postgres_url)
    authority = StaticWorkflowAuthority(
        grants={
            (BROKER_ID, ORGANIZATION_ID, RENEWAL_OUTREACH_KIND),
            (other_broker_id, other_organization_id, RENEWAL_OUTREACH_KIND),
        }
    )
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=authority,
    )
    trace = await control_plane.create_workflow(create_command())
    other_context = WorkflowCommandContext(
        actor_party_id=other_broker_id,
        organization_party_id=other_organization_id,
        cause_type="message",
        cause_id="cross-organization-read",
    )

    with pytest.raises(WorkflowNotFoundError):
        await control_plane.read_workflow_trace(trace.workflow.id, other_context)

    await database.dispose()


async def test_create_result_does_not_depend_on_post_commit_read_authority(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    class CreateOnlyAuthority:
        async def can_create_workflow(self, context, workflow_kind):
            return True

        async def can_read_workflow(self, context, workflow_id, workflow_kind, scope):
            return False

    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=CreateOnlyAuthority(),
    )

    trace = await control_plane.create_workflow(create_command())

    assert trace.workflow.status == "active"
    assert await workflow_count(migrated_postgres_url) == 1
    await database.dispose()


async def test_rejects_mismatched_renewal_period_without_mutation(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    original = renewal_proposal()
    draft = original.jobs[0].model_copy(
        update={
            "input": {
                "recipient_name": "John Smith",
                "renewal_period": "2025",
            }
        }
    )
    proposal = original.model_copy(update={"jobs": (draft, original.jobs[1])})

    with pytest.raises(InvalidWorkflowProposalError, match="renewal period"):
        await control_plane.create_workflow(create_command(proposal))

    assert await workflow_count(migrated_postgres_url) == 0


def test_job_proposal_rejects_caller_selected_execution_configuration():
    with pytest.raises(ValidationError):
        WorkflowJobProposal.model_validate(
            {
                "key": "draft",
                "kind": DRAFT_RENEWAL_EMAIL_KIND,
                "input": {"recipient_name": "John Smith", "renewal_period": "2026"},
                "executor": "named-agent",
                "max_attempts": 99,
                "status": "succeeded",
            }
        )


def test_registry_owns_the_only_v0_job_kinds():
    registry = default_workflow_registry()
    proposal = renewal_proposal()
    validated = registry.validate(proposal)

    assert validated.kind == RENEWAL_OUTREACH_KIND
    assert {job.kind for job in validated.jobs} == {
        DRAFT_RENEWAL_EMAIL_KIND,
        GMAIL_SEND_EMAIL_KIND,
    }
    assert {job.contract.max_attempts for job in validated.jobs} == {2, 3}
    assert {job.kind: job.contract.execution_strategy for job in validated.jobs} == {
        DRAFT_RENEWAL_EMAIL_KIND: ExecutionStrategy.FRESH_EXECUTION_AGENT,
        GMAIL_SEND_EMAIL_KIND: ExecutionStrategy.DETERMINISTIC_ADAPTER,
    }


def test_registry_rejects_duplicate_versioned_kind_names():
    class DuplicateWorkflowInput(BaseModel):
        pass

    duplicate = WorkflowKindContract(
        kind="duplicate.v1",
        input_schema=DuplicateWorkflowInput,
        allowed_job_kinds=frozenset(),
    )

    with pytest.raises(ValueError, match="duplicate Workflow Kind"):
        WorkflowKindRegistry((duplicate, duplicate), ())
