from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from example_insurance.migrations import apply_migrations
from openmagic_evals.evidence.audit import audit_cold_schema, audit_repository
from openmagic_evals.evidence.contracts import artifact_json_schema
from openmagic_evals.evidence.package_policy import PACKAGE_ROLES
from openmagic_evals.evidence.surface_contracts import (
    APPLICATION_PUBLIC_EXPORTS,
    PUBLIC_SURFACE_DIGESTS,
    RUNTIME_PUBLIC_EXPORTS,
)
from openmagic_evals.harness._postgres import postgres_container
from openmagic_runtime.kernel.inspection_types import (
    RuntimeAttempt,
    RuntimeInstance,
    RuntimeStep,
    RuntimeWait,
)

ROOT = Path(__file__).parents[2]


def test_repository_audit_closes_dependency_export_persistence_and_legacy_surfaces() -> None:
    report = audit_repository(ROOT)

    assert report.passed, report.violations
    assert report.audited_distributions == (
        "example-insurance",
        "openmagic-api",
        "openmagic-evals",
        "openmagic-playground",
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
        "openmagic_runtime.kernel._persistence",
    )
    assert RUNTIME_PUBLIC_EXPORTS["__init__.py"] == ("__version__",)
    assert RUNTIME_PUBLIC_EXPORTS["kernel/__init__.py"] == ()
    assert APPLICATION_PUBLIC_EXPORTS["__init__.py"] == ("__version__",)
    assert set(PUBLIC_SURFACE_DIGESTS) == set(report.audited_distributions)
    assert all(
        not hasattr(projection, "decode")
        for projection in (RuntimeAttempt, RuntimeInstance, RuntimeStep, RuntimeWait)
    )


def test_repository_audit_rejects_sql_outside_an_approved_owner(tmp_path: Path) -> None:
    for role in PACKAGE_ROLES:
        source = tmp_path / role.source
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROOT / role.source, source)
        project = tmp_path / role.project
        project.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / role.project, project)
    leaked = tmp_path / "reference-apps/example-insurance/src/example_insurance/renewal_policy.py"
    leaked.write_text('connection.execute("SELECT 1")\n__all__ = []\n', encoding="utf-8")

    report = audit_repository(tmp_path)

    assert not report.passed
    assert (
        "example-insurance contains SQL outside approved persistence owner renewal_policy.py:1"
        in report.violations
    )


def test_cold_schema_audit_accepts_only_current_owned_baselines() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)

        report = audit_cold_schema(database_url)

    assert report.passed, report.violations
    assert report.schemas == ("example_insurance", "openmagic_runtime", "public")
    assert report.tables["public"] == ()
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
