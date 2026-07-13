from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa

from server.workflows import (
    WorkflowDatabase,
    find_sms_party,
    resolve_sms_party,
    seed_v0_demo,
    sms_interaction_id,
)
from server.workflows.demo_seed import DEMO_POLICYHOLDER_ID

BROKER_ID = UUID("10000000-0000-0000-0000-000000000001")
ORGANIZATION_ID = UUID("20000000-0000-0000-0000-000000000001")


async def test_sms_phone_resolves_party_and_unknown_number_stays_provisional(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_v0_demo(
        migrated_postgres_url,
        broker_party_id=BROKER_ID,
        organization_party_id=ORGANIZATION_ID,
    )
    database = WorkflowDatabase(migrated_postgres_url)

    john = await resolve_sms_party(database, "+1 (416) 555-0142")
    unknown = await resolve_sms_party(database, "+1 (416) 555-0199")
    repeated_unknown = await resolve_sms_party(database, "+14165550199")

    assert john.party_id == DEMO_POLICYHOLDER_ID
    assert john.display_name == "John Smith"
    assert john.phone == "+14165550142"

    assert unknown.party_id == repeated_unknown.party_id
    assert unknown.display_name == "Caller 0199"
    assert unknown.phone == "+14165550199"
    assert sms_interaction_id("+1 (416) 555-0142") == sms_interaction_id("+14165550142")

    await database.dispose()


async def test_read_only_sms_lookup_does_not_create_an_unknown_party(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    database = WorkflowDatabase(migrated_postgres_url)

    assert await find_sms_party(database, "+1 (416) 555-0199") is None

    async with database.read_transaction() as session:
        party_count = await session.scalar(sa.text("SELECT count(*) FROM parties"))
    assert party_count == 0
    await database.dispose()
