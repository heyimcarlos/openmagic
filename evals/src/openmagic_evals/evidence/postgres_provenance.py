"""Capture exact PostgreSQL deployments while evidence is executing."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

from openmagic_evals.evidence.pins import PostgresDeploymentPin

_DIRECTORY_ENVIRONMENT = "OPENMAGIC_EVIDENCE_POSTGRES_DIRECTORY"


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _migration_head(connection: psycopg.Connection[tuple[object, ...]], table: str) -> str | None:
    exists = connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()
    if exists is None or exists[0] is None:
        return None
    schema, relation = table.split(".", maxsplit=1)
    row = connection.execute(
        sql.SQL("SELECT version FROM {}.{} ORDER BY version DESC LIMIT 1").format(
            sql.Identifier(schema),
            sql.Identifier(relation),
        )
    ).fetchone()
    return None if row is None else str(row[0])


def observe_postgres_deployment(
    database_url: str,
    *,
    postgres_image: str,
) -> PostgresDeploymentPin:
    """Observe one running database without exposing its URL or database name."""

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = connection.execute(
            "SELECT current_setting('server_version'), "
            "current_setting('transaction_isolation'), "
            "current_setting('synchronous_commit'), "
            "current_setting('TimeZone'), "
            "current_setting('max_connections'), "
            "current_database(), (pg_control_system()).system_identifier::text"
        ).fetchone()
        migration_heads = {
            "example_insurance": _migration_head(connection, "example_insurance.migration_history"),
            "openmagic_runtime": _migration_head(connection, "openmagic_runtime.migration_history"),
        }
    if row is None:
        raise RuntimeError("PostgreSQL did not return its observed provenance")
    configuration = {
        "max_connections": str(row[4]),
        "synchronous_commit": str(row[2]),
        "timezone": str(row[3]),
        "transaction_isolation": str(row[1]),
    }
    configuration_document = json.dumps(
        configuration, sort_keys=True, separators=(",", ":")
    ).encode()
    deployment_identity = json.dumps(
        {"database": str(row[5]), "system_identifier": str(row[6])},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return PostgresDeploymentPin(
        deployment_id=_sha256(deployment_identity),
        postgres_version=str(row[0]),
        postgres_image=postgres_image,
        postgres_configuration=configuration,
        postgres_configuration_digest=_sha256(configuration_document),
        migration_heads=migration_heads,
    )


def record_postgres_deployment(database_url: str, *, postgres_image: str) -> None:
    """Persist provenance only when an evidence runner has installed a recorder."""

    configured = os.environ.get(_DIRECTORY_ENVIRONMENT)
    if configured is None:
        return
    directory = Path(configured)
    directory.mkdir(parents=True, exist_ok=True)
    pin = observe_postgres_deployment(database_url, postgres_image=postgres_image)
    identity = pin.deployment_id.removeprefix("sha256:")
    target = directory / f"{identity}.json"
    temporary = directory / f".{identity}.{os.getpid()}.{uuid4().hex}.tmp"
    temporary.write_text(pin.model_dump_json() + "\n", encoding="utf-8")
    os.replace(temporary, target)


def load_postgres_deployments(directory: Path) -> tuple[PostgresDeploymentPin, ...]:
    """Load the exact set recorded by one evidence execution."""

    return tuple(
        PostgresDeploymentPin.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    )


@contextmanager
def record_postgres_deployments(directory: Path) -> Iterator[None]:
    """Direct all nested container observations into one lane-owned directory."""

    previous = os.environ.get(_DIRECTORY_ENVIRONMENT)
    os.environ[_DIRECTORY_ENVIRONMENT] = str(directory.resolve())
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(_DIRECTORY_ENVIRONMENT, None)
        else:
            os.environ[_DIRECTORY_ENVIRONMENT] = previous


__all__ = [
    "load_postgres_deployments",
    "observe_postgres_deployment",
    "record_postgres_deployment",
    "record_postgres_deployments",
]
