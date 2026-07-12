"""Explicit, idempotent identity and Workflow seed for the local V0 walkthrough."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .identity_models import (
    OrganizationMembershipRow,
    PartyIdentifierRow,
    PartyRow,
    WorkflowParticipantRoleRow,
    WorkflowParticipantRow,
)
from .models import WorkflowEventRow, WorkflowRow

DEMO_POLICYHOLDER_ID = UUID("30000000-0000-0000-0000-000000000001")
DEMO_WORKFLOW_ID = UUID("40000000-0000-0000-0000-000000000001")
DEMO_BROKER_IDENTIFIER_ID = UUID("51000000-0000-0000-0000-000000000001")
DEMO_ORGANIZATION_IDENTIFIER_ID = UUID("51000000-0000-0000-0000-000000000002")
DEMO_POLICYHOLDER_IDENTIFIER_ID = UUID("51000000-0000-0000-0000-000000000003")
DEMO_MEMBERSHIP_ID = UUID("52000000-0000-0000-0000-000000000001")
DEMO_BROKER_ROLE_ID = UUID("53000000-0000-0000-0000-000000000001")
DEMO_POLICYHOLDER_ROLE_ID = UUID("53000000-0000-0000-0000-000000000002")
DEMO_CREATED_EVENT_ID = UUID("54000000-0000-0000-0000-000000000001")


async def _add_if_missing(session: AsyncSession, row: object, identity: object) -> None:
    if await session.get(type(row), identity) is None:
        session.add(row)


async def seed_v0_demo(
    database_url: str,
    *,
    broker_party_id: UUID,
    organization_party_id: UUID,
) -> UUID:
    """Provision explicit trusted demo identity and one unplanned renewal Workflow."""

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        for row in (
            PartyRow(
                id=broker_party_id,
                kind="person",
                display_name="Carlos Broker",
            ),
            PartyRow(
                id=organization_party_id,
                kind="organization",
                display_name="Acme Brokerage",
            ),
            PartyRow(
                id=DEMO_POLICYHOLDER_ID,
                kind="person",
                display_name="John Smith",
            ),
        ):
            await _add_if_missing(session, row, row.id)
        await session.flush()
        for row in (
            PartyIdentifierRow(
                id=DEMO_BROKER_IDENTIFIER_ID,
                party_id=broker_party_id,
                kind="email",
                value="broker@acme.example",
                verified_at=now,
            ),
            PartyIdentifierRow(
                id=DEMO_ORGANIZATION_IDENTIFIER_ID,
                party_id=organization_party_id,
                kind="organization_ref",
                value="acme-brokerage",
                verified_at=now,
            ),
            PartyIdentifierRow(
                id=DEMO_POLICYHOLDER_IDENTIFIER_ID,
                party_id=DEMO_POLICYHOLDER_ID,
                kind="email",
                value="john@example.com",
                verified_at=now,
            ),
            OrganizationMembershipRow(
                id=DEMO_MEMBERSHIP_ID,
                person_party_id=broker_party_id,
                organization_party_id=organization_party_id,
                granted_at=now,
            ),
        ):
            await _add_if_missing(session, row, row.id)
        await session.flush()
        workflow = WorkflowRow(
            id=DEMO_WORKFLOW_ID,
            kind="renewal_outreach.v1",
            objective="2026 renewal outreach for John Smith",
            status="active",
            input={"renewal_period": "2026"},
            organization_party_id=organization_party_id,
        )
        await _add_if_missing(session, workflow, workflow.id)
        await session.flush()
        for row in (
            WorkflowParticipantRow(
                workflow_id=DEMO_WORKFLOW_ID,
                party_id=broker_party_id,
            ),
            WorkflowParticipantRow(
                workflow_id=DEMO_WORKFLOW_ID,
                party_id=DEMO_POLICYHOLDER_ID,
            ),
        ):
            await _add_if_missing(session, row, (row.workflow_id, row.party_id))
        await session.flush()
        for row in (
            WorkflowParticipantRoleRow(
                id=DEMO_BROKER_ROLE_ID,
                workflow_id=DEMO_WORKFLOW_ID,
                party_id=broker_party_id,
                role="Broker",
                granted_at=now,
            ),
            WorkflowParticipantRoleRow(
                id=DEMO_POLICYHOLDER_ROLE_ID,
                workflow_id=DEMO_WORKFLOW_ID,
                party_id=DEMO_POLICYHOLDER_ID,
                role="Policyholder",
                granted_at=now,
            ),
            WorkflowEventRow(
                id=DEMO_CREATED_EVENT_ID,
                workflow_id=DEMO_WORKFLOW_ID,
                event_type="workflow_created",
                actor_type="party",
                actor_id=str(broker_party_id),
                cause_type="fixture",
                cause_id="v0-demo-seed",
                data={},
            ),
        ):
            await _add_if_missing(session, row, row.id)
    await engine.dispose()
    return DEMO_WORKFLOW_ID
