from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from uuid import uuid4

import psycopg
import pytest
from example_insurance.migrations import apply_migrations
from example_insurance.reset import (
    ResetPreflightBlocked,
    assess_reset,
    reset_synthetic_deployment,
)
from openmagic_evals.harness._postgres import postgres_container


@pytest.mark.integration
def test_cold_migrations_create_independently_owned_schemas_without_reverse_foreign_keys() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        first = apply_migrations(database_url)
        second = apply_migrations(database_url)

        assert [(bundle.schema, bundle.versions) for bundle in first] == [
            (
                "openmagic_runtime",
                (
                    "0001_runtime_baseline",
                    "0002_renewal_drafting_runtime",
                    "0003_fenced_effect_kernel",
                ),
            ),
            (
                "example_insurance",
                (
                    "0001_example_insurance_baseline",
                    "0002_renewal_drafting_application",
                    "0003_renewal_approval_effect",
                    "0004_deterministic_verification",
                ),
            ),
        ]
        assert all(not bundle.versions for bundle in second)

        with psycopg.connect(database_url) as connection:
            histories = connection.execute(
                "SELECT 'openmagic_runtime', version FROM openmagic_runtime.migration_history "
                "UNION ALL "
                "SELECT 'example_insurance', version FROM example_insurance.migration_history "
                "ORDER BY 1, 2"
            ).fetchall()
            reverse_foreign_keys = connection.execute(
                "SELECT constraint_name FROM information_schema.referential_constraints "
                "WHERE constraint_schema = 'openmagic_runtime' "
                "AND unique_constraint_schema = 'example_insurance'"
            ).fetchall()
            invalid_singleton_constraints = connection.execute(
                "SELECT tc.table_schema, tc.table_name, kcu.column_name "
                "FROM information_schema.table_constraints AS tc "
                "JOIN information_schema.key_column_usage AS kcu "
                "ON kcu.constraint_schema = tc.constraint_schema "
                "AND kcu.constraint_name = tc.constraint_name "
                "WHERE tc.constraint_type = 'UNIQUE' AND (("
                "tc.table_schema = 'openmagic_runtime' AND tc.table_name = 'deliveries' "
                "AND kcu.column_name = 'domain_event_id') OR ("
                "tc.table_schema = 'example_insurance' AND tc.table_name = 'renewal_drafts' "
                "AND kcu.column_name = 'workflow_id'))"
            ).fetchall()
            draft_step_uniqueness = connection.execute(
                "SELECT count(*) FROM information_schema.table_constraints AS tc "
                "JOIN information_schema.key_column_usage AS kcu "
                "ON kcu.constraint_schema = tc.constraint_schema "
                "AND kcu.constraint_name = tc.constraint_name "
                "WHERE tc.constraint_type = 'UNIQUE' "
                "AND tc.table_schema = 'example_insurance' "
                "AND tc.table_name = 'renewal_drafts' AND kcu.column_name = 'step_id'"
            ).fetchone()

        assert histories == [
            ("example_insurance", "0001_example_insurance_baseline"),
            ("example_insurance", "0002_renewal_drafting_application"),
            ("example_insurance", "0003_renewal_approval_effect"),
            ("example_insurance", "0004_deterministic_verification"),
            ("openmagic_runtime", "0001_runtime_baseline"),
            ("openmagic_runtime", "0002_renewal_drafting_runtime"),
            ("openmagic_runtime", "0003_fenced_effect_kernel"),
        ]
        assert reverse_foreign_keys == []
        assert invalid_singleton_constraints == []
        assert draft_step_uniqueness == (1,)


@pytest.mark.integration
def test_reset_preflight_rejects_unknown_data_and_rebuilds_accepted_synthetic_data() -> None:
    demo_workflow_id = uuid4()
    demo_party_id = uuid4()
    unexpected_workflow_id = uuid4()
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        with psycopg.connect(database_url) as connection:
            connection.execute("CREATE TABLE public.workflows (id uuid PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE public.interaction_causes "
                "(id text PRIMARY KEY, actor_party_id uuid NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE public.interaction_activity_receipts "
                "(id uuid PRIMARY KEY, cause_id text NOT NULL, workflow_id uuid)"
            )
            connection.execute("CREATE TABLE public.customer_records (payload text NOT NULL)")
            connection.execute(
                "INSERT INTO public.workflows (id) VALUES (%s), (%s)",
                (demo_workflow_id, unexpected_workflow_id),
            )
            connection.execute(
                "INSERT INTO public.interaction_causes (id, actor_party_id) VALUES (%s, %s)",
                ("demo-cause", demo_party_id),
            )
            connection.execute(
                "INSERT INTO public.interaction_activity_receipts "
                "(id, cause_id, workflow_id) VALUES (%s, %s, %s)",
                (uuid4(), "demo-cause", demo_workflow_id),
            )
            connection.execute("INSERT INTO public.customer_records (payload) VALUES ('real')")

        blocked = assess_reset(
            database_url,
            demo_workflow_ids=(demo_workflow_id,),
            demo_party_ids=(demo_party_id,),
        )
        assert not blocked.accepted
        assert blocked.unexpected_records == (("customer_records", 1), ("workflows", 1))

        with psycopg.connect(database_url) as connection:
            connection.execute(
                "DELETE FROM public.workflows WHERE id = %s",
                (unexpected_workflow_id,),
            )
            connection.execute("DELETE FROM public.customer_records")

        racing_connection = psycopg.connect(database_url)
        try:
            racing_connection.execute(
                "INSERT INTO public.customer_records (payload) VALUES ('committed during reset')"
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                reset = executor.submit(
                    reset_synthetic_deployment,
                    database_url,
                    demo_workflow_ids=(demo_workflow_id,),
                    demo_party_ids=(demo_party_id,),
                )
                with pytest.raises(FutureTimeoutError):
                    reset.result(timeout=0.2)
                racing_connection.commit()
                with pytest.raises(ResetPreflightBlocked):
                    reset.result(timeout=5)
        finally:
            racing_connection.close()

        with psycopg.connect(database_url) as connection:
            connection.execute("DELETE FROM public.customer_records")

        accepted = assess_reset(
            database_url,
            demo_workflow_ids=(demo_workflow_id,),
            demo_party_ids=(demo_party_id,),
        )
        assert accepted.accepted
        reset_synthetic_deployment(
            database_url,
            demo_workflow_ids=(demo_workflow_id,),
            demo_party_ids=(demo_party_id,),
        )

        with psycopg.connect(database_url) as connection:
            result = connection.execute(
                "SELECT to_regclass('public.workflows'), "
                "to_regnamespace('openmagic_runtime'), to_regnamespace('example_insurance')"
            ).fetchone()
        assert result is not None
        assert result[0] is None
        assert result[1] is not None
        assert result[2] is not None
