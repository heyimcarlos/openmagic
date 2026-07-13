from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.tests.workflows.factories import BROKER_ID, ORGANIZATION_ID
from server.workflows import (
    RENEWAL_OUTREACH_KIND,
    StaticWorkflowAuthority,
    WorkflowControlPlane,
    WorkflowDatabase,
    default_workflow_registry,
)
from server.workflows.identity_models import (
    OrganizationMembershipRow,
    PartyIdentifierRow,
    PartyRow,
)
from server.workflows.models import InteractionCauseRow


@pytest.fixture
async def seeded_workflow_identity(migrated_postgres_url: str, clean_workflow_database):
    engine = create_async_engine(migrated_postgres_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        session.add_all(
            [
                PartyRow(id=BROKER_ID, kind="person", display_name="Carlos Broker"),
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
                    content_digest="fixture-authenticated-cause",
                ),
            ]
        )
    await engine.dispose()


@pytest.fixture
async def control_plane(migrated_postgres_url: str, seeded_workflow_identity):
    database = WorkflowDatabase(migrated_postgres_url)
    authority = StaticWorkflowAuthority(
        grants={(BROKER_ID, ORGANIZATION_ID, RENEWAL_OUTREACH_KIND)}
    )
    yield WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=authority,
    )
    await database.dispose()
