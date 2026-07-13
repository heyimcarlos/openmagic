"""Local walkthrough controls for visualizing durable Workflow backpressure."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.config import Settings, get_settings
from server.services.backpressure_demo import BackpressureDemoService, BackpressureSnapshot

router = APIRouter(prefix="/demo/backpressure", tags=["demo"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]


class EnqueueDemoJobsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_count: int = Field(ge=2, le=40)

    @model_validator(mode="after")
    def require_complete_renewal_graphs(self) -> EnqueueDemoJobsRequest:
        if self.job_count % 2:
            raise ValueError("job_count must be even")
        return self


def _service(settings: Settings) -> BackpressureDemoService:
    if settings.interaction_mode != "workflow":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The backpressure demo requires Workflow mode",
        )
    try:
        return _cached_service(
            settings.database_url or "",
            settings.workflow_broker_party_id or "",
            settings.workflow_organization_party_id or "",
            settings.demo_broker_email,
            settings.demo_policyholder_email,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@lru_cache(maxsize=4)
def _cached_service(
    database_url: str,
    broker_party_id: str,
    organization_party_id: str,
    broker_email: str,
    policyholder_email: str,
) -> BackpressureDemoService:
    return BackpressureDemoService(
        Settings(
            database_url=database_url,
            workflow_broker_party_id=broker_party_id,
            workflow_organization_party_id=organization_party_id,
            demo_broker_email=broker_email,
            demo_policyholder_email=policyholder_email,
            interaction_mode="workflow",
        )
    )


@router.get("", response_model=BackpressureSnapshot)
async def backpressure_snapshot(
    settings: SettingsDependency,
) -> BackpressureSnapshot:
    return await _service(settings).snapshot()


@router.post("/jobs", response_model=BackpressureSnapshot)
async def enqueue_demo_jobs(
    payload: EnqueueDemoJobsRequest,
    settings: SettingsDependency,
) -> BackpressureSnapshot:
    return await _service(settings).enqueue_jobs(payload.job_count)


__all__ = ["router"]
