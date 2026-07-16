from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from example_insurance.migrations import apply_migrations
from openmagic_evals.evidence.postgres_provenance import (
    load_postgres_deployments,
    record_postgres_deployments,
)
from openmagic_evals.harness._postgres import POSTGRES_IMAGE, postgres_container


def test_container_records_its_own_exact_postgres_provenance(tmp_path: Path) -> None:
    provenance_directory = tmp_path / "postgres"
    with (
        record_postgres_deployments(provenance_directory),
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)

    deployments = load_postgres_deployments(provenance_directory)

    assert len(deployments) == 1
    deployment = deployments[0]
    assert deployment.postgres_image == POSTGRES_IMAGE
    assert deployment.postgres_version.startswith("17.")
    assert deployment.migration_heads == {
        "example_insurance": "0004_deterministic_verification",
        "openmagic_runtime": "0003_fenced_effect_kernel",
    }
    serialized = (next(provenance_directory.glob("*.json"))).read_text(encoding="utf-8")
    assert database_url not in serialized
    assert "openmagic_test_" not in serialized
