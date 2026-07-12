"""Small deterministic retrieval evaluation for the V0 renewal landscape."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from server.tests.workflows.retrieval_fixtures import (
    BROKER_ID,
    TARGET_ID,
    seed_retrieval_landscape,
)
from server.workflows import (
    WorkflowDatabase,
    WorkflowInspectionContext,
    WorkflowRetrieval,
    WorkflowSearchRequest,
)


@pytest.fixture
async def retrieval(migrated_postgres_url: str, clean_workflow_database):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    yield WorkflowRetrieval(database=database, cursor_secret=b"retrieval-eval-secret")
    await database.dispose()


async def test_target_is_found_early_with_bounded_context(
    retrieval: WorkflowRetrieval,
    record_property: Callable[[str, object], None],
):
    requests = (
        WorkflowSearchRequest(
            query="John Smith renewal",
            workflow_kind="renewal_outreach.v1",
            status="active",
            organization="Acme Brokerage",
            renewal_period="2026",
            limit=3,
        ),
        WorkflowSearchRequest(
            participant="John Smith",
            workflow_kind="renewal_outreach.v1",
            organization="Acme Brokerage",
            limit=3,
        ),
        WorkflowSearchRequest(query="John renewal", limit=3),
    )
    ranks: list[int] = []
    response_sizes: list[int] = []

    for request in requests:
        page = await retrieval.search_workflows(
            WorkflowInspectionContext(actor_party_id=BROKER_ID),
            request,
        )
        ranks.append(
            next(
                index
                for index, result in enumerate(page.results, start=1)
                if result.workflow_id == TARGET_ID
            )
        )
        response_sizes.append(len(page.model_dump_json().encode()))

    hit_at_1 = sum(rank <= 1 for rank in ranks) / len(ranks)
    hit_at_3 = sum(rank <= 3 for rank in ranks) / len(ranks)
    mean_reciprocal_rank = sum(1 / rank for rank in ranks) / len(ranks)
    approximate_token_burdens = [size // 4 for size in response_sizes]
    record_property("hit_at_1", hit_at_1)
    record_property("hit_at_3", hit_at_3)
    record_property("mean_reciprocal_rank", mean_reciprocal_rank)
    record_property("max_response_bytes", max(response_sizes))
    record_property("max_approximate_tokens", max(approximate_token_burdens))

    assert hit_at_1 == 1.0
    assert hit_at_3 == 1.0
    assert mean_reciprocal_rank == 1.0
    assert max(response_sizes) < 10_000
    assert max(approximate_token_burdens) < 2_500
