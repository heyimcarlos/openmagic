from __future__ import annotations

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from server.tests.workflows.retrieval_fixtures import (
    BROKER_ID,
    TARGET_ID,
    UNAUTHORIZED_ID,
    seed_retrieval_landscape,
)
from server.workflows import (
    InvalidWorkflowSearchError,
    StaleWorkflowCursorError,
    WorkflowDatabase,
    WorkflowInspectionContext,
    WorkflowRetrieval,
    WorkflowSearchRequest,
)


@pytest.fixture
async def retrieval(migrated_postgres_url: str, clean_workflow_database):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    yield WorkflowRetrieval(database=database, cursor_secret=b"issue-18-test-secret")
    await database.dispose()


async def test_search_ranks_target_and_hides_unauthorized_landscape(retrieval: WorkflowRetrieval):
    page = await retrieval.search_workflows(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        WorkflowSearchRequest(
            query="John Smith renewal",
            workflow_kind="renewal_outreach.v1",
            status="active",
            renewal_period="2026",
            limit=10,
        ),
    )

    assert page.results[0].workflow_id == TARGET_ID
    assert UNAUTHORIZED_ID not in {result.workflow_id for result in page.results}
    assert page.total_matches == 2
    assert page.has_more is False
    assert page.next_cursor is None
    assert page.applied_filters == {
        "workflow_kind": "renewal_outreach.v1",
        "status": "active",
        "renewal_period": "2026",
    }
    assert {entry.value: entry.count for entry in page.facets.organization.entries} == {
        "Acme Brokerage": 1,
        "Northwind Brokerage": 1,
    }
    assert all(
        "Hidden Brokerage" not in reason
        for result in page.results
        for reason in result.match_reasons
    )
    encoded = page.model_dump_json()
    assert str(BROKER_ID) not in encoded


async def test_search_accepts_full_human_request_and_relational_identifiers(
    retrieval: WorkflowRetrieval,
):
    natural_language = await retrieval.search_workflows(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        WorkflowSearchRequest(
            query="Can you prepare John Smith's 2026 renewal email at Acme Brokerage?"
        ),
    )
    descriptive = await retrieval.search_workflows(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        WorkflowSearchRequest(query="Please prepare John Smith's urgent renewal"),
    )
    identifier = await retrieval.search_workflows(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        WorkflowSearchRequest(
            participant="john@example.com",
            workflow_kind="renewal_outreach.v1",
            status="active",
        ),
    )

    assert natural_language.total_matches > 0
    assert natural_language.results[0].workflow_id == TARGET_ID
    assert descriptive.total_matches > 0
    assert descriptive.results[0].workflow_id == TARGET_ID
    assert identifier.total_matches == 1
    assert identifier.results[0].workflow_id == TARGET_ID
    assert "exact participant identifier match" in identifier.results[0].match_reasons


async def test_renewal_period_filter_is_scoped_to_declaring_workflow_kind(
    retrieval: WorkflowRetrieval,
):
    page = await retrieval.search_workflows(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        WorkflowSearchRequest(renewal_period="2026", limit=10),
    )

    assert page.total_matches == 3
    assert {result.workflow_kind for result in page.results} == {"renewal_outreach.v1"}


async def test_partial_identifiers_are_explained_without_exact_rank_or_wildcards(
    retrieval: WorkflowRetrieval,
):
    partial = await retrieval.search_workflows(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        WorkflowSearchRequest(organization="acme", limit=10),
    )
    wildcard = await retrieval.search_workflows(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        WorkflowSearchRequest(organization="%", limit=10),
    )

    assert partial.total_matches == 4
    assert all(
        any(reason.startswith("organization filter matched") for reason in result.match_reasons)
        for result in partial.results
    )
    assert all(
        "exact organization identifier match" not in result.match_reasons
        for result in partial.results
    )
    assert wildcard.total_matches == 0


async def test_search_cursor_is_bounded_and_bound_to_normalized_request(
    retrieval: WorkflowRetrieval,
):
    request = WorkflowSearchRequest(
        workflow_kind="renewal_outreach.v1",
        status="active",
        organization="Acme Brokerage",
        renewal_period="2026",
        limit=1,
    )

    first = await retrieval.search_workflows(
        WorkflowInspectionContext(actor_party_id=BROKER_ID), request
    )
    assert first.total_matches == 2
    assert first.has_more is True
    assert first.next_cursor is not None
    second = await retrieval.search_workflows(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        request.model_copy(update={"organization": "acme brokerage", "cursor": first.next_cursor}),
    )
    assert second.results[0].workflow_id != first.results[0].workflow_id

    with pytest.raises(StaleWorkflowCursorError):
        await retrieval.search_workflows(
            WorkflowInspectionContext(actor_party_id=BROKER_ID),
            request.model_copy(update={"status": "completed", "cursor": first.next_cursor}),
        )


async def test_invalid_search_inputs_create_or_change_nothing(
    retrieval: WorkflowRetrieval,
    migrated_postgres_url: str,
):
    with pytest.raises(ValidationError):
        WorkflowSearchRequest()
    with pytest.raises(ValidationError):
        WorkflowSearchRequest(query="%")
    with pytest.raises(ValidationError):
        WorkflowSearchRequest(participant="   ")
    with pytest.raises(InvalidWorkflowSearchError):
        await retrieval.search_workflows(
            WorkflowInspectionContext(actor_party_id=BROKER_ID),
            WorkflowSearchRequest(workflow_kind="unknown.v1"),
        )
    with pytest.raises(InvalidWorkflowSearchError):
        await retrieval.search_workflows(
            WorkflowInspectionContext(actor_party_id=BROKER_ID),
            WorkflowSearchRequest(query="renewal", cursor="tampered"),
        )
    no_match = await retrieval.search_workflows(
        WorkflowInspectionContext(actor_party_id=BROKER_ID),
        WorkflowSearchRequest(query="does-not-exist"),
    )
    assert no_match.results == ()
    assert no_match.total_matches == 0

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        job_count = await connection.scalar(sa.text("SELECT count(*) FROM workflow_jobs"))
        event_count = await connection.scalar(sa.text("SELECT count(*) FROM workflow_events"))
    await engine.dispose()
    assert job_count == 0
    assert event_count == 6
