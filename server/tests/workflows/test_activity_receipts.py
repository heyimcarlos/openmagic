from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from server.tests.workflows.factories import BROKER_ID
from server.workflows import (
    InteractionActivityAction,
    InteractionActivityStatus,
    InteractionActivityStore,
    WorkflowDatabase,
)


async def test_activity_receipts_are_ordered_and_expose_only_sanitized_fields(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    database = WorkflowDatabase(migrated_postgres_url)
    store = InteractionActivityStore(database)

    first = await store.start(
        cause_id="message-renewal-request",
        action=InteractionActivityAction.SEARCH_WORKFLOWS,
    )
    second = await store.start(
        cause_id="message-renewal-request",
        action=InteractionActivityAction.READ_WORKFLOW_PACKET,
    )
    await store.finish(first.id, status=InteractionActivityStatus.SUCCEEDED)
    await store.finish(second.id, status=InteractionActivityStatus.FAILED)

    receipts = await store.list_for_actor_causes(
        actor_party_id=BROKER_ID,
        cause_ids=["message-renewal-request"],
    )

    assert [(receipt.sequence, receipt.action, receipt.status) for receipt in receipts] == [
        (1, InteractionActivityAction.SEARCH_WORKFLOWS, InteractionActivityStatus.SUCCEEDED),
        (2, InteractionActivityAction.READ_WORKFLOW_PACKET, InteractionActivityStatus.FAILED),
    ]
    assert set(receipts[0].__dataclass_fields__) == {
        "id",
        "cause_id",
        "sequence",
        "action",
        "status",
        "workflow_id",
        "created_at",
        "finished_at",
    }
    await database.dispose()


async def test_concurrent_receipts_serialize_sequence_and_reads_are_actor_scoped(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    database = WorkflowDatabase(migrated_postgres_url)
    store = InteractionActivityStore(database)

    receipts = await asyncio.gather(
        *(
            store.start(
                cause_id="message-renewal-request",
                action=InteractionActivityAction.SEARCH_WORKFLOWS,
            )
            for _ in range(5)
        )
    )

    assert sorted(receipt.sequence for receipt in receipts) == [1, 2, 3, 4, 5]
    assert (
        await store.list_for_actor_causes(
            actor_party_id=uuid4(),
            cause_ids=["message-renewal-request"],
        )
        == ()
    )
    await database.dispose()


async def test_unknown_cause_cannot_start_activity_receipt(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    database = WorkflowDatabase(migrated_postgres_url)
    store = InteractionActivityStore(database)

    with pytest.raises(LookupError, match="Interaction Cause"):
        await store.start(
            cause_id="unknown-cause",
            action=InteractionActivityAction.SEARCH_WORKFLOWS,
        )
    await database.dispose()


async def test_activity_receipt_cannot_link_an_unknown_workflow(
    migrated_postgres_url: str,
    seeded_workflow_identity,
):
    database = WorkflowDatabase(migrated_postgres_url)
    store = InteractionActivityStore(database)
    receipt = await store.start(
        cause_id="message-renewal-request",
        action=InteractionActivityAction.PROPOSE_RENEWAL_EMAIL,
    )

    with pytest.raises(IntegrityError):
        await store.finish(
            receipt.id,
            status=InteractionActivityStatus.SUCCEEDED,
            workflow_id=uuid4(),
        )

    current = (
        await store.list_for_actor_causes(
            actor_party_id=BROKER_ID,
            cause_ids=["message-renewal-request"],
        )
    )[0]
    assert current.status is InteractionActivityStatus.RUNNING
    assert current.workflow_id is None
    await database.dispose()
