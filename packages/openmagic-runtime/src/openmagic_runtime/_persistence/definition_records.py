"""Canonical immutable Workflow Definition records."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def register_definition(
    database_url: str,
    *,
    definition_key: str,
    definition_version: int,
    manifest: dict[str, Any],
    manifest_digest: str,
) -> str | None:
    with (
        psycopg.connect(database_url) as connection,
        connection.transaction(),
        connection.cursor(row_factory=dict_row) as cursor,
    ):
        inserted = cursor.execute(
            "INSERT INTO openmagic_runtime.workflow_definitions "
            "(definition_key, definition_version, manifest, manifest_digest) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING manifest_digest",
            (
                definition_key,
                definition_version,
                Jsonb(manifest),
                manifest_digest,
            ),
        ).fetchone()
        if inserted is not None:
            return str(inserted["manifest_digest"])
        existing = cursor.execute(
            "SELECT manifest_digest FROM openmagic_runtime.workflow_definitions "
            "WHERE definition_key = %s AND definition_version = %s FOR UPDATE",
            (definition_key, definition_version),
        ).fetchone()
    return None if existing is None else str(existing["manifest_digest"])


__all__ = ["register_definition"]
