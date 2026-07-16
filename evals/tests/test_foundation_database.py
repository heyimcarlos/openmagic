from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest
from example_insurance.migrations import apply_migrations
from openmagic_evals.evidence.audit import audit_cold_schema
from openmagic_evals.harness._postgres import postgres_container
from openmagic_evals.harness.synthetic_reset import (
    ResetPreflightBlocked,
    assess_reset,
    mark_synthetic_deployment,
    reset_synthetic_deployment,
)


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
            protected_command_checks = connection.execute(
                "SELECT pg_get_constraintdef(constraint_row.oid) "
                "FROM pg_constraint AS constraint_row "
                "JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid "
                "JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace "
                "WHERE schema_row.nspname = 'example_insurance' "
                "AND table_row.relname = 'protected_commands' "
                "AND constraint_row.contype = 'c'"
            ).fetchall()
            challenge_constraints = connection.execute(
                "SELECT constraint_row.contype, pg_get_constraintdef(constraint_row.oid) "
                "FROM pg_constraint AS constraint_row "
                "JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid "
                "JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace "
                "WHERE schema_row.nspname = 'example_insurance' "
                "AND table_row.relname = 'verification_challenges'"
            ).fetchall()
            redundant_pending_index = connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'example_insurance' "
                "AND indexname = 'one_pending_exact_verification_challenge'"
            ).fetchall()

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
        assert any(
            definition.count("outcome IS NOT NULL") == 2
            for (definition,) in protected_command_checks
        )
        assert ("u", "UNIQUE (protected_command_id)") in challenge_constraints
        assert any(
            kind == "f"
            and definition.startswith(
                "FOREIGN KEY (protected_command_id, party_id, thread_id, "
                "protected_workflow_id, purpose)"
            )
            for kind, definition in challenge_constraints
        )
        assert redundant_pending_index == []


@pytest.mark.integration
def test_reset_preflight_rejects_unowned_relations_and_rebuilds_synthetic_data() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        mark_synthetic_deployment(database_url)
        with psycopg.connect(database_url) as connection:
            connection.execute("CREATE TABLE public.customer_records (payload text NOT NULL)")
            connection.execute("INSERT INTO public.customer_records (payload) VALUES ('unknown')")

        blocked = assess_reset(database_url)
        assert not blocked.accepted
        assert blocked.blocking_conditions == ("public schema contains application tables",)
        with pytest.raises(ResetPreflightBlocked, match="public schema"):
            reset_synthetic_deployment(database_url)

        with psycopg.connect(database_url) as connection:
            connection.execute("DROP TABLE public.customer_records")

        accepted = assess_reset(database_url)
        assert accepted.accepted
        reset_synthetic_deployment(database_url)

        with psycopg.connect(database_url) as connection:
            result = connection.execute(
                "SELECT to_regnamespace('openmagic_runtime'), to_regnamespace('example_insurance')"
            ).fetchone()
        assert result is not None
        assert result[0] is not None
        assert result[1] is not None


@pytest.mark.integration
def test_reset_preflight_rejects_a_database_without_an_explicit_synthetic_name() -> None:
    with postgres_container(database_name=f"openmagic_release_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)

        assessment = assess_reset(database_url)

        assert not assessment.accepted
        assert assessment.blocking_conditions == (
            "database name is not explicitly synthetic",
            "deployment is not durably marked synthetic",
        )
        with pytest.raises(ResetPreflightBlocked, match="not explicitly synthetic"):
            reset_synthetic_deployment(database_url)


@pytest.mark.integration
def test_reset_preflight_rejects_an_unmarked_database_with_a_synthetic_name() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)

        assessment = assess_reset(database_url)

        assert not assessment.accepted
        assert "deployment is not durably marked synthetic" in assessment.blocking_conditions
        with pytest.raises(ResetPreflightBlocked, match="durably marked synthetic"):
            reset_synthetic_deployment(database_url)


@pytest.mark.integration
def test_cold_schema_audit_rejects_every_extra_user_schema_or_public_table() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        assert audit_cold_schema(database_url).passed

        with psycopg.connect(database_url) as connection:
            connection.execute("CREATE SCHEMA rogue_owner")
            connection.execute("CREATE TABLE public.rogue_table (identity uuid PRIMARY KEY)")

        audit = audit_cold_schema(database_url)

        assert not audit.passed
        assert set(audit.schemas) == {
            "example_insurance",
            "openmagic_runtime",
            "public",
            "rogue_owner",
        }
        assert audit.tables["public"] == ("rogue_table",)
