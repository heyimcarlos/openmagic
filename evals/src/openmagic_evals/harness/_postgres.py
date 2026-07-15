from __future__ import annotations

from testcontainers.postgres import PostgresContainer


def postgres_container(*, database_name: str) -> PostgresContainer:
    return PostgresContainer(
        "postgres:17-alpine",
        username="openmagic",
        password="openmagic",
        dbname=database_name,
        driver=None,
    )
