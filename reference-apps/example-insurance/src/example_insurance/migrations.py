"""Application-owned orchestration for installed migration bundles."""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from importlib import resources
from importlib.metadata import entry_points
from typing import LiteralString, Protocol, cast

import psycopg
from psycopg import Connection, sql


class _MigrationBundle(Protocol):
    owner: str
    schema: str
    resource_package: str


@dataclass(frozen=True)
class AppliedMigrationBundle:
    owner: str
    schema: str
    versions: tuple[str, ...]


_EXPECTED_BUNDLES = ("openmagic_runtime", "example_insurance")


def _apply_bundle(
    connection: Connection[tuple[object, ...]], bundle: _MigrationBundle
) -> tuple[str, ...]:
    connection.execute(
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(bundle.schema))
    )
    history = sql.SQL("{}.migration_history").format(sql.Identifier(bundle.schema))
    connection.execute(
        sql.SQL(
            "CREATE TABLE IF NOT EXISTS {} ("
            "version text PRIMARY KEY, digest text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now()"
            ")"
        ).format(history)
    )
    applied: list[str] = []
    migration_root = resources.files(bundle.resource_package)
    for migration in sorted(
        (item for item in migration_root.iterdir() if item.name.endswith(".sql")),
        key=lambda item: item.name,
    ):
        version = migration.name.removesuffix(".sql")
        source = migration.read_text(encoding="utf-8")
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        existing = connection.execute(
            sql.SQL("SELECT digest FROM {} WHERE version = %s").format(history),
            (version,),
        ).fetchone()
        if existing is not None:
            if existing[0] != digest:
                raise RuntimeError(
                    f"packaged migration changed after application: {bundle.owner}:{version}"
                )
            continue
        connection.execute(sql.SQL(cast(LiteralString, source)))
        connection.execute(
            sql.SQL("INSERT INTO {} (version, digest) VALUES (%s, %s)").format(history),
            (version, digest),
        )
        applied.append(version)
    return tuple(applied)


def apply_migrations(database_url: str) -> tuple[AppliedMigrationBundle, ...]:
    """Apply the installed runtime baseline before the application baseline."""

    with psycopg.connect(database_url) as connection, connection.transaction():
        return apply_migrations_on(connection)


def apply_migrations_on(
    connection: Connection[tuple[object, ...]],
) -> tuple[AppliedMigrationBundle, ...]:
    """Apply both owned bundles inside the caller-owned transaction."""

    discovered = {entry.name: entry for entry in entry_points(group="openmagic.migrations")}
    missing = tuple(name for name in _EXPECTED_BUNDLES if name not in discovered)
    if missing:
        raise RuntimeError(f"required migration bundles are not installed: {', '.join(missing)}")

    results: list[AppliedMigrationBundle] = []
    for name in _EXPECTED_BUNDLES:
        factory = discovered[name].load()
        bundle: _MigrationBundle = factory()
        results.append(
            AppliedMigrationBundle(
                owner=bundle.owner,
                schema=bundle.schema,
                versions=_apply_bundle(connection, bundle),
            )
        )
    return tuple(results)


def main() -> None:
    parser = argparse.ArgumentParser(prog="example-insurance-migrate")
    parser.add_argument("--database-url", required=True)
    arguments = parser.parse_args()
    for result in apply_migrations(arguments.database_url):
        versions = ",".join(result.versions) if result.versions else "current"
        print(f"{result.owner}: {result.schema} ({versions})")


__all__ = ["AppliedMigrationBundle", "apply_migrations", "apply_migrations_on", "main"]
