from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from example_insurance.migrations import apply_migrations
from openmagic_evals.evidence.postgres_provenance import (
    load_postgres_deployments,
    record_postgres_deployments,
)
from openmagic_evals.harness._postgres import (
    POSTGRES_IMAGE,
    ObservedPostgresContainer,
    postgres_container,
)


class _ContainerControlProbe:
    def __init__(
        self,
        *,
        start_error: BaseException | None = None,
        stop_error: BaseException | None = None,
    ) -> None:
        self.start_error = start_error
        self.stop_error = stop_error
        self.stop_calls = 0

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error

    def stop(self) -> None:
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error

    def get_connection_url(self, *, driver: str | None = None) -> str:
        return "postgresql://synthetic:synthetic@127.0.0.1:1/unavailable"


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
    assert deployment.postgres_configuration["default_transaction_isolation"] == "read committed"
    assert deployment.postgres_configuration["observer_transaction_isolation"] == "repeatable read"
    assert deployment.migration_heads == {
        "example_insurance": "0004_deterministic_verification",
        "openmagic_runtime": "0003_fenced_effect_kernel",
    }
    serialized = (next(provenance_directory.glob("*.json"))).read_text(encoding="utf-8")
    assert database_url not in serialized
    assert "openmagic_test_" not in serialized


def test_startup_failure_retains_cleanup_ownership_until_stop_succeeds() -> None:
    control = _ContainerControlProbe(
        start_error=RuntimeError("startup failed"),
        stop_error=RuntimeError("cleanup failed"),
    )
    postgres = ObservedPostgresContainer(database_name="unused", container=control)

    with pytest.raises(BaseExceptionGroup, match="startup and cleanup failed") as raised:
        postgres.start()
    assert len(raised.value.exceptions) == 2

    control.stop_error = None
    postgres.stop()
    postgres.stop()
    assert control.stop_calls == 2


def test_stop_aggregates_provenance_and_cleanup_before_retrying(tmp_path: Path) -> None:
    control = _ContainerControlProbe(stop_error=RuntimeError("cleanup failed"))
    postgres = ObservedPostgresContainer(database_name="unused", container=control).start()

    with (
        record_postgres_deployments(tmp_path / "provenance"),
        pytest.raises(BaseExceptionGroup, match="evidence cleanup failed") as raised,
    ):
        postgres.stop()
    assert len(raised.value.exceptions) == 2

    control.stop_error = None
    with (
        record_postgres_deployments(tmp_path / "provenance"),
        pytest.raises(psycopg.OperationalError),
    ):
        postgres.stop()
    postgres.stop()
    assert control.stop_calls == 2
