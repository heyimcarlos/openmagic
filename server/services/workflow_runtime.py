"""Background delivery loop for durable Workflow Jobs and Notifications."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from functools import lru_cache

from server.agents.execution_agent.workflow_draft import FreshDraftExecutionAgentFactory
from server.agents.interaction_agent.workflow_notifications import FreshWorkflowInteractionFactory
from server.config import Settings, get_settings
from server.logging_config import logger
from server.workflows import (
    StaticWorkflowAuthority,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowRetrieval,
    default_workflow_registry,
)
from server.workflows.worker import NotificationWorker, WorkflowWorker


class WorkflowRuntimeService:
    """Poll one Job and one Notification at a time in Workflow mode."""

    def __init__(self, settings: Settings, poll_interval_seconds: float = 1.0) -> None:
        self._settings = settings
        self._poll_interval = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._database: WorkflowDatabase | None = None
        self._job_worker: WorkflowWorker | None = None
        self._notification_worker: NotificationWorker | None = None

    async def start(self) -> None:
        if self._settings.interaction_mode != "workflow":
            return
        if self._task is not None and not self._task.done():
            return
        if not self._settings.database_url or not self._settings.workflow_cursor_secret:
            logger.warning(
                "Workflow runtime disabled because PostgreSQL configuration is incomplete"
            )
            return
        database = WorkflowDatabase(self._settings.database_url)
        control_plane = WorkflowControlPlane(
            database=database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(grants=set()),
        )
        retrieval = WorkflowRetrieval(
            database=database,
            cursor_secret=self._settings.workflow_cursor_secret.encode(),
        )
        self._database = database
        self._job_worker = WorkflowWorker(
            control_plane=control_plane,
            draft_runtimes=FreshDraftExecutionAgentFactory(self._settings),
            worker_id="workflow-worker",
            application_build=self._settings.app_version,
        )
        self._notification_worker = NotificationWorker(
            control_plane=control_plane,
            interactions=FreshWorkflowInteractionFactory(database=database, retrieval=retrieval),
            worker_id="notification-worker",
        )
        self._running = True
        self._task = asyncio.create_task(self._run(), name="workflow-runtime")
        logger.info("Workflow runtime started", extra={"interval": self._poll_interval})

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._database is not None:
            await self._database.dispose()
        self._database = None
        self._job_worker = None
        self._notification_worker = None

    async def _run(self) -> None:
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - long-running service boundary
                logger.exception(
                    "Workflow runtime tick failed",
                    extra={"error_type": type(exc).__name__},
                )
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        if self._job_worker is None or self._notification_worker is None:
            return
        await self._job_worker.run_once()
        await self._notification_worker.run_once()


@lru_cache(maxsize=1)
def get_workflow_runtime_service() -> WorkflowRuntimeService:
    return WorkflowRuntimeService(get_settings())


__all__ = ["WorkflowRuntimeService", "get_workflow_runtime_service"]
