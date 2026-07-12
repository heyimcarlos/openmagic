from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from server.tests.workflows.retrieval_fixtures import ACME_ID, BROKER_ID
from server.workflows import WorkflowDatabase, WorkflowInspectionContext, WorkflowRetrieval
from server.workflows.demo_seed import DEMO_WORKFLOW_ID, seed_v0_demo
from server.workflows.retrieval_contracts import WorkflowSearchRequest


async def test_demo_seed_is_explicit_idempotent_and_searchable(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_v0_demo(
        migrated_postgres_url,
        broker_party_id=BROKER_ID,
        organization_party_id=ACME_ID,
    )
    await seed_v0_demo(
        migrated_postgres_url,
        broker_party_id=BROKER_ID,
        organization_party_id=ACME_ID,
    )

    database = WorkflowDatabase(migrated_postgres_url)
    retrieval = WorkflowRetrieval(database=database, cursor_secret=b"demo-seed-test")
    page = await retrieval.search_workflows(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        WorkflowSearchRequest(query="John Smith 2026 renewal Acme"),
    )
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        workflow_count = await connection.scalar(sa.text("SELECT count(*) FROM workflows"))
        job_count = await connection.scalar(sa.text("SELECT count(*) FROM workflow_jobs"))
    await engine.dispose()
    await database.dispose()

    assert workflow_count == 1
    assert job_count == 0
    assert page.total_matches == 1
    assert page.results[0].workflow_id == DEMO_WORKFLOW_ID
