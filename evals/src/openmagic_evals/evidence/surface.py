"""Canonical source, installed-wheel, and cold-schema closure artifact."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from example_insurance.migrations import apply_migrations

from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.audit import audit_cold_schema, audit_repository
from openmagic_evals.evidence.contracts import (
    ColdSchemaEvidence,
    InstalledSurfaceEvidence,
    RepositorySurfaceEvidence,
    SurfaceAuditArtifact,
    SurfaceAuditSummary,
    canonical_digest,
)
from openmagic_evals.evidence.installed_audit import audit_installed_environment
from openmagic_evals.evidence.reproducibility import reproducibility_pin
from openmagic_evals.evidence.surface_contracts import (
    APPLICATION_PUBLIC_EXPORTS,
    EXPECTED_PRODUCTION_EDGES,
    RUNTIME_PUBLIC_EXPORTS,
)
from openmagic_evals.harness._postgres import postgres_container


def run_surface_audit(
    *,
    repository_root: Path,
    output: Path,
    timeout_seconds: int = 120,
) -> SurfaceAuditArtifact:
    root = repository_root.resolve()
    command = (
        "openmagic-evidence",
        "audit-surface",
        "--repository-root",
        str(root),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    started_at = datetime.now(UTC)
    repository = audit_repository(root)
    installed = audit_installed_environment()
    with postgres_container(database_name="openmagic_test_surface_audit") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        cold = audit_cold_schema(database_url)
    finished_at = datetime.now(UTC)
    strict_pass = repository.passed and installed.passed and cold.passed
    artifact = SurfaceAuditArtifact(
        reproducibility=reproducibility_pin(
            root,
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=canonical_digest(
                {
                    "application_public_exports": APPLICATION_PUBLIC_EXPORTS,
                    "expected_production_edges": EXPECTED_PRODUCTION_EDGES,
                    "runtime_public_exports": RUNTIME_PUBLIC_EXPORTS,
                    "expected_cold_schemas": ["example_insurance", "openmagic_runtime", "public"],
                    "expected_migration_heads": {
                        "example_insurance": "0004_deterministic_verification",
                        "openmagic_runtime": "0003_fenced_effect_kernel",
                    },
                }
            ),
        ),
        repository=RepositorySurfaceEvidence(**asdict(repository)),
        installed=InstalledSurfaceEvidence(**asdict(installed)),
        cold_schema=ColdSchemaEvidence(**asdict(cold)),
        summary=SurfaceAuditSummary(
            repository_passed=repository.passed,
            installed_surface_passed=installed.passed,
            cold_schema_passed=cold.passed,
            strict_pass=strict_pass,
        ),
        limitations=(
            "The surface audit applies only to the pinned source and installed distributions.",
            "The cold schema audit covers one fresh PostgreSQL deployment.",
        ),
    )
    write_artifact(output, artifact)
    if not strict_pass:
        raise RuntimeError("public-surface or cold-schema closure failed")
    return artifact


__all__ = ["run_surface_audit"]
