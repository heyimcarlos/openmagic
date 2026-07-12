from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="session")
def postgres_url() -> Generator[str]:
    configured_url = os.getenv("OPENMAGIC_TEST_DATABASE_URL")
    if configured_url:
        yield _psycopg_url(configured_url)
        return

    with PostgresContainer("postgres:17-alpine", driver="psycopg") as postgres:
        yield _psycopg_url(postgres.get_connection_url())


@pytest.fixture(scope="session")
def migrated_postgres_url(postgres_url: str) -> str:
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
    command.upgrade(config, "head")
    return postgres_url


@pytest.fixture
async def clean_workflow_database(migrated_postgres_url: str):
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "TRUNCATE notifications, workflow_events, interaction_causes, workflow_job_runs, "
                "workflow_job_dependencies, workflow_jobs, "
                "workflow_participant_roles, workflow_participants, "
                "organization_memberships, party_identifiers, workflows, parties CASCADE"
            )
        )
    yield
    await engine.dispose()
