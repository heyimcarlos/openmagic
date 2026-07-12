from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ORGANIZATION_ID = UUID("71000000-0000-0000-0000-000000000001")
WORKFLOW_ID = UUID("72000000-0000-0000-0000-000000000001")
EVENT_ID = UUID("73000000-0000-0000-0000-000000000001")
NOTIFICATION_ID = UUID("74000000-0000-0000-0000-000000000001")


def test_upgrade_backfills_existing_delivered_notification(
    postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("OPENMAGIC_DATABASE_URL", raising=False)
    database_name = "openmagic_notification_migration_test"
    source_url = make_url(postgres_url)
    admin = sa.create_engine(
        source_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as connection:
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        connection.execute(sa.text(f'CREATE DATABASE "{database_name}"'))
    migration_url = source_url.set(database=database_name)
    config = Config(PROJECT_ROOT / "alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        migration_url.render_as_string(hide_password=False).replace("%", "%%"),
    )
    command.upgrade(config, "d815c00eb002")
    engine = sa.create_engine(migration_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "TRUNCATE notifications, workflow_events, workflow_job_runs, "
                    "workflow_job_dependencies, workflow_jobs, workflow_participant_roles, "
                    "workflow_participants, organization_memberships, party_identifiers, "
                    "workflows, parties CASCADE"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO parties (id, kind, display_name) "
                    "VALUES (:id, 'organization', 'Migration Test')"
                ),
                {"id": ORGANIZATION_ID},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO workflows "
                    "(id, kind, objective, status, input, organization_party_id) "
                    "VALUES (:id, 'renewal_outreach.v1', 'Migration test', 'active', "
                    '\'{"renewal_period": "2026"}\'::jsonb, :organization_id)'
                ),
                {"id": WORKFLOW_ID, "organization_id": ORGANIZATION_ID},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO workflow_events "
                    "(id, workflow_id, event_type, actor_type, actor_id, "
                    "cause_type, cause_id, data) "
                    "VALUES (:id, :workflow_id, 'draft_ready', 'run', 'legacy-run', "
                    "'job', 'legacy-job', '{}'::jsonb)"
                ),
                {"id": EVENT_ID, "workflow_id": WORKFLOW_ID},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO notifications "
                    "(id, workflow_id, workflow_event_id, kind, destination_type, "
                    "destination_id, status, attempts, max_attempts, available_at, delivered_at) "
                    "VALUES (:id, :workflow_id, :event_id, 'approval_required', 'party', "
                    "'legacy-party', 'delivered', 1, 3, now(), now())"
                ),
                {"id": NOTIFICATION_ID, "workflow_id": WORKFLOW_ID, "event_id": EVENT_ID},
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            delivered_by = connection.scalar(
                sa.text("SELECT delivered_by FROM notifications WHERE id = :id"),
                {"id": NOTIFICATION_ID},
            )
        assert delivered_by == "legacy_delivery"
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()
