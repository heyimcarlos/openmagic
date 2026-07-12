"""Run-scoped execution and Notification delivery coordinators."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from .contracts import (
    AcknowledgeNotificationCommand,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    NotificationDeliveryPacket,
    ReportNotificationFailureCommand,
    ReportRunResultCommand,
    RunResult,
    WorkflowExecutionPacket,
)
from .control_plane import WorkflowControlPlane


class DraftExecutionRuntime(Protocol):
    """One fresh, bounded drafting runtime."""

    @property
    def runtime_instance_id(self) -> UUID: ...

    async def execute(self, execution_input: dict[str, object]) -> RunResult: ...


class DraftExecutionRuntimeFactory(Protocol):
    """Construct and dispose one runtime for one claimed Draft Run."""

    def create(
        self,
        runtime_instance_id: UUID,
    ) -> AbstractAsyncContextManager[DraftExecutionRuntime]: ...


class NotificationInteraction(Protocol):
    """One fresh Interaction Agent turn bound to no prior prompt context."""

    async def handle(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
    ) -> None: ...


class NotificationInteractionFactory(Protocol):
    """Construct and dispose one Interaction Agent per Notification attempt."""

    def create(self) -> AbstractAsyncContextManager[NotificationInteraction]: ...


class WorkflowWorker:
    """Claim and execute at most one Job per tick."""

    def __init__(
        self,
        *,
        control_plane: WorkflowControlPlane,
        executors: Mapping[str, DraftExecutionRuntimeFactory],
        worker_id: str,
        application_build: str,
        lease_duration: timedelta = timedelta(minutes=5),
    ) -> None:
        self._control_plane = control_plane
        self._executors = dict(executors)
        if not self._executors:
            raise ValueError("Workflow Worker requires at least one installed executor")
        self._worker_id = worker_id
        self._application_build = application_build
        self._lease_duration = lease_duration

    async def run_once(self) -> WorkflowExecutionPacket | None:
        """Claim one Job, run it outside PostgreSQL, then report its result."""

        packet = await self._control_plane.claim_job(
            ClaimWorkflowJobCommand(
                worker_id=self._worker_id,
                application_build=self._application_build,
                lease_duration=self._lease_duration,
                executor_keys=tuple(self._executors),
            )
        )
        if packet is None:
            return None
        factory = self._executors.get(packet.executor_key)
        if factory is None or packet.runtime_instance_id is None:
            raise RuntimeError(f"No executor is installed for {packet.executor_key!r}")

        try:
            async with factory.create(packet.runtime_instance_id) as runtime:
                if runtime.runtime_instance_id != packet.runtime_instance_id:
                    raise RuntimeError("Runtime identity does not match the claimed Run")
                result = await runtime.execute(packet.input)
        except Exception:
            result = RunResult(
                outcome="failed",
                evidence=({"type": "executor_failed"},),
                error={"code": "executor_unavailable"},
            )

        await self._control_plane.report_run_result(
            ReportRunResultCommand(run_id=packet.run_id, result=result)
        )
        return packet


class NotificationWorker:
    """Deliver one Notification, then acknowledge only completed handling."""

    def __init__(
        self,
        *,
        control_plane: WorkflowControlPlane,
        interactions: NotificationInteractionFactory,
        worker_id: str,
        lease_duration: timedelta = timedelta(minutes=5),
    ) -> None:
        self._control_plane = control_plane
        self._interactions = interactions
        self._worker_id = worker_id
        self._lease_duration = lease_duration

    async def run_once(self) -> NotificationDeliveryPacket | None:
        packet = await self._control_plane.claim_notification(
            ClaimNotificationCommand(
                worker_id=self._worker_id,
                lease_duration=self._lease_duration,
            )
        )
        if packet is None:
            return None
        try:
            async with self._interactions.create() as interaction:
                await interaction.handle(
                    packet.notification_id,
                    packet.workflow_event_id,
                    packet.workflow_id,
                )
        except Exception:
            await self._control_plane.report_notification_failure(
                ReportNotificationFailureCommand(
                    notification_id=packet.notification_id,
                    worker_id=self._worker_id,
                    delivery_attempt=packet.delivery_attempt,
                    error_code="interaction_delivery_failed",
                )
            )
            raise
        return await self._control_plane.acknowledge_notification(
            AcknowledgeNotificationCommand(
                notification_id=packet.notification_id,
                worker_id=self._worker_id,
                delivery_attempt=packet.delivery_attempt,
            )
        )
