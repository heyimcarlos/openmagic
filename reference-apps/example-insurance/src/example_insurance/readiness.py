"""Application-owned deployment readiness checks."""

from __future__ import annotations

import psycopg


def verify_application_ready(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        installed = connection.execute(
            "SELECT to_regnamespace('example_insurance') IS NOT NULL"
        ).fetchone()
    if installed is None or not installed[0]:
        raise RuntimeError("Example Insurance schema is not installed")


__all__ = ["verify_application_ready"]
