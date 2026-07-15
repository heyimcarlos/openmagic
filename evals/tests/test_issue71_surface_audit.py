from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from example_insurance.migrations import apply_migrations
from openmagic_evals.evidence.audit import audit_cold_schema, audit_repository
from openmagic_evals.evidence.contracts import artifact_json_schema
from openmagic_evals.harness._postgres import postgres_container

ROOT = Path(__file__).parents[2]


def test_repository_audit_closes_dependency_export_persistence_and_legacy_surfaces() -> None:
    report = audit_repository(ROOT)

    assert report.passed, report.violations
    assert report.audited_distributions == (
        "example-insurance",
        "openmagic-api",
        "openmagic-evals",
        "openmagic-runtime",
    )
    assert report.production_dependency_edges == (
        "example-insurance -> openmagic-runtime",
        "openmagic-api -> example-insurance",
        "openmagic-api -> openmagic-runtime",
    )
    assert report.private_persistence_packages == (
        "example_insurance._persistence",
        "openmagic_runtime._persistence",
    )


def test_cold_schema_audit_accepts_only_current_owned_baselines() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)

        report = audit_cold_schema(database_url)

    assert report.passed, report.violations
    assert report.schemas == ("example_insurance", "openmagic_runtime")
    assert report.legacy_relations == ()
    assert report.migration_heads == {
        "example_insurance": "0004_deterministic_verification",
        "openmagic_runtime": "0003_fenced_effect_kernel",
    }


def test_versioned_schema_and_public_evidence_commands_are_reproducible() -> None:
    schema = artifact_json_schema()
    assert schema["$defs"]
    assert "openmagic.enterprise-evidence.v1" in json.dumps(schema, sort_keys=True)

    completed = subprocess.run(
        [sys.executable, "-m", "openmagic_evals.evidence", "schema"],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == schema

    help_result = subprocess.run(
        [sys.executable, "-m", "openmagic_evals.evidence", "--help"],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    for command in (
        "agent-quality",
        "audit-surface",
        "claim-report",
        "demo-renewal",
        "demo-verification",
        "deterministic",
        "live-smoke",
        "playground",
        "processes",
        "races",
        "schema",
    ):
        assert command in help_result.stdout
