"""Local walkthrough controls for visualizing durable Workflow backpressure."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.config import Settings, get_settings
from server.services import (
    BackpressureDemoService,
    BackpressureSnapshot,
    get_backpressure_demo_service,
    get_workflow_runtime_service,
)

router = APIRouter(prefix="/demo/backpressure", tags=["demo"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]


class EnqueueDemoWorkflowsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_count: int = Field(ge=1, le=50)
    scenario: Literal["mixed", "renewal", "claim", "policy"] = "mixed"


class EnqueueDemoJobsRequest(BaseModel):
    """Compatibility command used by the original /system presentation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_count: int = Field(ge=2, le=100)

    @model_validator(mode="after")
    def require_complete_renewal_graphs(self) -> EnqueueDemoJobsRequest:
        if self.job_count % 2:
            raise ValueError("job_count must be even")
        return self


def _service(settings: Settings) -> BackpressureDemoService:
    if not settings.enable_backpressure_demo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The backpressure demo is not enabled",
        )
    if settings.interaction_mode != "workflow":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The backpressure demo requires Workflow mode",
        )
    try:
        return get_backpressure_demo_service(
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


@router.get("", response_model=BackpressureSnapshot)
async def backpressure_snapshot(
    settings: SettingsDependency,
) -> BackpressureSnapshot:
    return await _service(settings).snapshot()


@router.post("/workflows", response_model=BackpressureSnapshot)
async def enqueue_demo_workflows(
    payload: EnqueueDemoWorkflowsRequest,
    settings: SettingsDependency,
) -> BackpressureSnapshot:
    return await _service(settings).enqueue_workflows(
        payload.workflow_count,
        payload.scenario,
    )


@router.post("/jobs", response_model=BackpressureSnapshot)
async def enqueue_demo_jobs(
    payload: EnqueueDemoJobsRequest,
    settings: SettingsDependency,
) -> BackpressureSnapshot:
    return await _service(settings).enqueue_workflows(
        payload.job_count // 2,
        "renewal",
    )


@router.post("/workers", response_model=BackpressureSnapshot)
async def add_demo_worker(settings: SettingsDependency) -> BackpressureSnapshot:
    service = _service(settings)
    try:
        get_workflow_runtime_service().add_demo_worker()
    except (PermissionError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return await service.snapshot()


@router.delete("/workers/{worker_id}", response_model=BackpressureSnapshot)
async def remove_demo_worker(
    worker_id: str,
    settings: SettingsDependency,
) -> BackpressureSnapshot:
    service = _service(settings)
    try:
        get_workflow_runtime_service().remove_demo_worker(worker_id)
    except (PermissionError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return await service.snapshot()


__all__ = ["router"]
