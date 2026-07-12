from __future__ import annotations

import pytest

from server.tests.workflows.factories import BROKER_ID, ORGANIZATION_ID
from server.workflows import (
    RENEWAL_OUTREACH_KIND,
    StaticWorkflowAuthority,
    WorkflowControlPlane,
    WorkflowDatabase,
    default_workflow_registry,
)


@pytest.fixture
async def control_plane(migrated_postgres_url: str, clean_workflow_database):
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
