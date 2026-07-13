"""Synthetic authorization and ranking fixtures for Workflow retrieval tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.workflows import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    ProposeWorkflowJobsCommand,
    WorkflowCommandContext,
    WorkflowJobProposal,
)
from server.workflows.identity_models import (
    OrganizationMembershipRow,
    PartyIdentifierRow,
    PartyRow,
    WorkflowParticipantRoleRow,
    WorkflowParticipantRow,
)
from server.workflows.models import InteractionCauseRow, WorkflowEventRow, WorkflowRow

BROKER_ID = UUID("10000000-0000-0000-0000-000000000001")
OTHER_BROKER_ID = UUID("10000000-0000-0000-0000-000000000002")
ACME_ID = UUID("20000000-0000-0000-0000-000000000001")
NORTHWIND_ID = UUID("20000000-0000-0000-0000-000000000002")
HIDDEN_ORG_ID = UUID("20000000-0000-0000-0000-000000000003")
JOHN_ACME_ID = UUID("30000000-0000-0000-0000-000000000001")
JOHN_NORTHWIND_ID = UUID("30000000-0000-0000-0000-000000000002")
JANE_ID = UUID("30000000-0000-0000-0000-000000000003")

TARGET_ID = UUID("40000000-0000-0000-0000-000000000001")
HISTORICAL_ID = UUID("40000000-0000-0000-0000-000000000002")
SAME_NAME_ID = UUID("40000000-0000-0000-0000-000000000003")
WRONG_KIND_ID = UUID("40000000-0000-0000-0000-000000000004")
OTHER_POLICYHOLDER_ID = UUID("40000000-0000-0000-0000-000000000005")
UNAUTHORIZED_ID = UUID("40000000-0000-0000-0000-000000000006")


async def seed_retrieval_landscape(database_url: str) -> None:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    parties = (
        PartyRow(id=BROKER_ID, kind="person", display_name="Carlos Broker"),
        PartyRow(id=OTHER_BROKER_ID, kind="person", display_name="Hidden Broker"),
        PartyRow(id=ACME_ID, kind="organization", display_name="Acme Brokerage"),
        PartyRow(id=NORTHWIND_ID, kind="organization", display_name="Northwind Brokerage"),
        PartyRow(id=HIDDEN_ORG_ID, kind="organization", display_name="Hidden Brokerage"),
        PartyRow(id=JOHN_ACME_ID, kind="person", display_name="John Smith"),
        PartyRow(id=JOHN_NORTHWIND_ID, kind="person", display_name="John Smith"),
        PartyRow(id=JANE_ID, kind="person", display_name="Jane Doe"),
    )
    async with sessions.begin() as session:
        session.add_all(parties)
        await session.flush()
        session.add_all(
            [
                PartyIdentifierRow(
                    party_id=BROKER_ID,
                    kind="email",
                    value="broker@acme.example",
                    verified_at=now,
                ),
                PartyIdentifierRow(
                    party_id=OTHER_BROKER_ID,
                    kind="email",
                    value="hidden@example.test",
                    verified_at=now,
                ),
                PartyIdentifierRow(
                    party_id=ACME_ID,
                    kind="organization_ref",
                    value="acme-brokerage",
                    verified_at=now,
                ),
                PartyIdentifierRow(
                    party_id=NORTHWIND_ID,
                    kind="organization_ref",
                    value="northwind-brokerage",
                    verified_at=now,
                ),
                PartyIdentifierRow(
                    party_id=JOHN_ACME_ID,
                    kind="email",
                    value="john@example.com",
                    verified_at=now,
                ),
                PartyIdentifierRow(
                    party_id=JOHN_NORTHWIND_ID,
                    kind="email",
                    value="john@northwind.example",
                    verified_at=now,
                ),
                PartyIdentifierRow(
                    party_id=JANE_ID,
                    kind="email",
                    value="jane@example.com",
                    verified_at=now,
                ),
                OrganizationMembershipRow(
                    person_party_id=BROKER_ID,
                    organization_party_id=ACME_ID,
                    granted_at=now,
                ),
                OrganizationMembershipRow(
                    person_party_id=BROKER_ID,
                    organization_party_id=NORTHWIND_ID,
                    granted_at=now,
                ),
                OrganizationMembershipRow(
                    person_party_id=OTHER_BROKER_ID,
                    organization_party_id=HIDDEN_ORG_ID,
                    granted_at=now,
                ),
                InteractionCauseRow(
                    id="renewal-request-message",
                    cause_type="message",
                    actor_party_id=BROKER_ID,
                    content_digest=(
                        "9893389911c1defb3c7cc8fa1366ff57c5c2c5ebc2ab075889a697b1c950c9b9"
                    ),
                ),
            ]
        )
        await session.flush()

        workflows = (
            (
                TARGET_ID,
                "renewal_outreach.v1",
                "2026 renewal outreach for John Smith",
                "active",
                "2026",
                ACME_ID,
                JOHN_ACME_ID,
                now,
            ),
            (
                HISTORICAL_ID,
                "renewal_outreach.v1",
                "Detailed historical 2025 renewal for John Smith",
                "completed",
                "2025",
                ACME_ID,
                JOHN_ACME_ID,
                now - timedelta(days=365),
            ),
            (
                SAME_NAME_ID,
                "renewal_outreach.v1",
                "2026 renewal outreach for John Smith",
                "active",
                "2026",
                NORTHWIND_ID,
                JOHN_NORTHWIND_ID,
                now - timedelta(minutes=1),
            ),
            (
                WRONG_KIND_ID,
                "claim_intake.v1",
                "Active claim for John Smith",
                "active",
                "2026",
                ACME_ID,
                JOHN_ACME_ID,
                now - timedelta(minutes=2),
            ),
            (
                OTHER_POLICYHOLDER_ID,
                "renewal_outreach.v1",
                "2026 renewal outreach for Jane Doe",
                "active",
                "2026",
                ACME_ID,
                JANE_ID,
                now - timedelta(minutes=3),
            ),
            (
                UNAUTHORIZED_ID,
                "renewal_outreach.v1",
                "John Smith exact urgent 2026 renewal outreach",
                "active",
                "2026",
                HIDDEN_ORG_ID,
                JOHN_ACME_ID,
                now + timedelta(minutes=1),
            ),
        )
        for (
            workflow_id,
            kind,
            objective,
            status,
            period,
            organization_id,
            policyholder_id,
            created_at,
        ) in workflows:
            broker_id = OTHER_BROKER_ID if workflow_id == UNAUTHORIZED_ID else BROKER_ID
            session.add(
                WorkflowRow(
                    id=workflow_id,
                    kind=kind,
                    objective=objective,
                    status=status,
                    input={"renewal_period": period},
                    organization_party_id=organization_id,
                    created_at=created_at,
                )
            )
            await session.flush()
            session.add_all(
                [
                    WorkflowParticipantRow(workflow_id=workflow_id, party_id=broker_id),
                    WorkflowParticipantRow(workflow_id=workflow_id, party_id=policyholder_id),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    WorkflowParticipantRoleRow(
                        workflow_id=workflow_id,
                        party_id=broker_id,
                        role="Broker",
                        granted_at=created_at,
                    ),
                    WorkflowParticipantRoleRow(
                        workflow_id=workflow_id,
                        party_id=policyholder_id,
                        role="Policyholder",
                        granted_at=created_at,
                    ),
                    WorkflowEventRow(
                        workflow_id=workflow_id,
                        event_type="workflow_created",
                        actor_type="party",
                        actor_id=str(broker_id),
                        cause_type="fixture",
                        cause_id=f"fixture-{workflow_id}",
                        data={},
                        occurred_at=created_at,
                    ),
                ]
            )
    await engine.dispose()


def renewal_job_command() -> ProposeWorkflowJobsCommand:
    return ProposeWorkflowJobsCommand(
        context=WorkflowCommandContext(
            actor_party_id=BROKER_ID,
            organization_party_id=ACME_ID,
            cause_type="message",
            cause_id="renewal-request-message",
        ),
        workflow_id=TARGET_ID,
        jobs=(
            WorkflowJobProposal(
                key="draft",
                kind=DRAFT_RENEWAL_EMAIL_KIND,
                input={"recipient_name": "John Smith", "renewal_period": "2026"},
            ),
            WorkflowJobProposal(
                key="send",
                kind=GMAIL_SEND_EMAIL_KIND,
                input={
                    "sender_mailbox": "broker@acme.example",
                    "to": ["john@example.com"],
                    "subject": {"job_output": "draft", "field": "subject"},
                    "body": {"job_output": "draft", "field": "body"},
                },
                depends_on=("draft",),
            ),
        ),
    )
