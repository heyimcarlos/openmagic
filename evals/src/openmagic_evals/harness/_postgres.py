from __future__ import annotations

from types import TracebackType
from typing import Protocol

from testcontainers.postgres import PostgresContainer

from openmagic_evals.evidence.postgres_provenance import record_postgres_deployment

POSTGRES_IMAGE = "postgres@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"


class _PostgresContainerControl(Protocol):
    def start(self) -> object: ...

    def stop(self) -> object: ...

    def get_connection_url(self, *, driver: str | None = None) -> str: ...


class ObservedPostgresContainer:
    """Own a testcontainer and record that exact deployment before shutdown."""

    def __init__(
        self,
        *,
        database_name: str,
        container: _PostgresContainerControl | None = None,
    ) -> None:
        self._container = container or PostgresContainer(
            POSTGRES_IMAGE,
            username="openmagic",
            password="openmagic",
            dbname=database_name,
            driver=None,
        )
        self._owns_container = False
        self._ready = False

    def __enter__(self) -> ObservedPostgresContainer:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.stop()
        except BaseException as cleanup_error:
            if exc_value is None:
                raise
            raise BaseExceptionGroup(
                "PostgreSQL evidence execution and cleanup failed",
                [exc_value, cleanup_error],
            ) from exc_value

    def start(self) -> ObservedPostgresContainer:
        if self._owns_container:
            raise RuntimeError("PostgreSQL evidence container is already running")
        self._owns_container = True
        try:
            self._container.start()
        except BaseException as startup_error:
            try:
                self._stop_container()
            except BaseException as cleanup_error:
                raise BaseExceptionGroup(
                    "PostgreSQL evidence startup and cleanup failed",
                    [startup_error, cleanup_error],
                ) from startup_error
            raise
        self._ready = True
        return self

    def stop(self) -> None:
        if not self._owns_container:
            return
        errors: list[BaseException] = []
        try:
            if self._ready:
                record_postgres_deployment(
                    self.get_connection_url(driver=None),
                    postgres_image=POSTGRES_IMAGE,
                )
        except BaseException as provenance_error:
            errors.append(provenance_error)
        try:
            self._stop_container()
        except BaseException as cleanup_error:
            errors.append(cleanup_error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("PostgreSQL evidence cleanup failed", errors)

    def _stop_container(self) -> None:
        self._container.stop()
        self._owns_container = False
        self._ready = False

    def get_connection_url(self, *, driver: str | None = None) -> str:
        return self._container.get_connection_url(driver=driver)


def postgres_container(*, database_name: str) -> ObservedPostgresContainer:
    return ObservedPostgresContainer(database_name=database_name)


__all__ = ["POSTGRES_IMAGE", "ObservedPostgresContainer", "postgres_container"]
