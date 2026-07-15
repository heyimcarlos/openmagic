from __future__ import annotations

from testcontainers.postgres import PostgresContainer

POSTGRES_IMAGE = "postgres@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"


def postgres_container(*, database_name: str) -> PostgresContainer:
    return PostgresContainer(
        POSTGRES_IMAGE,
        username="openmagic",
        password="openmagic",
        dbname=database_name,
        driver=None,
    )


__all__ = ["POSTGRES_IMAGE", "postgres_container"]
