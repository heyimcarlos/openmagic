from __future__ import annotations

from types import TracebackType

from testcontainers.postgres import PostgresContainer

from openmagic_evals.evidence.postgres_provenance import record_postgres_deployment

POSTGRES_IMAGE = "postgres@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"


class ObservedPostgresContainer:
    """Own a testcontainer and record that exact deployment before shutdown."""

    def __init__(self, *, database_name: str) -> None:
        self._container = PostgresContainer(
            POSTGRES_IMAGE,
            username="openmagic",
            password="openmagic",
            dbname=database_name,
            driver=None,
        )
        self._started = False

    def __enter__(self) -> ObservedPostgresContainer:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def start(self) -> ObservedPostgresContainer:
        if self._started:
            raise RuntimeError("PostgreSQL evidence container is already running")
        self._container.start()
        self._started = True
        return self

    def stop(self) -> None:
        if not self._started:
            return
        try:
            record_postgres_deployment(
                self.get_connection_url(driver=None),
                postgres_image=POSTGRES_IMAGE,
            )
        finally:
            self._started = False
            self._container.stop()

    def get_connection_url(self, *, driver: str | None = None) -> str:
        return self._container.get_connection_url(driver=driver)


def postgres_container(*, database_name: str) -> ObservedPostgresContainer:
    return ObservedPostgresContainer(database_name=database_name)


__all__ = ["POSTGRES_IMAGE", "ObservedPostgresContainer", "postgres_container"]
