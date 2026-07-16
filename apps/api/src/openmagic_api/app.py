"""Installed FastAPI process composition."""

from __future__ import annotations

from example_insurance import __version__ as application_version
from example_insurance.readiness import verify_application_ready
from fastapi import FastAPI
from openmagic_runtime import __version__ as runtime_version
from openmagic_runtime.evidence import inspect_runtime_database

from openmagic_api import __version__ as api_version
from openmagic_api.renewals import StartRenewalRequest, StartRenewalResponse, submit_renewal


def create_app(*, database_url: str) -> FastAPI:
    app = FastAPI(title="OpenMagic API", version=api_version)

    @app.get("/health")
    def health() -> dict[str, object]:
        verify_application_ready(database_url)
        payload = inspect_runtime_database(database_url).as_dict()
        payload["role"] = "api"
        payload["runtime_version"] = runtime_version
        payload["application_version"] = application_version
        return payload

    @app.post("/renewals", response_model=StartRenewalResponse)
    def start_renewal(request: StartRenewalRequest) -> StartRenewalResponse:
        return submit_renewal(database_url=database_url, request=request)

    return app


__all__ = ["create_app"]
