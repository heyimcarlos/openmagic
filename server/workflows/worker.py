"""Run-scoped execution and Notification delivery coordinators."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from .contracts import (
    AcknowledgeNotificationCommand,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    NotificationDeliveryPacket,
    ReportRunResultCommand,
    RunResult,
    WorkflowExecutionPacket,
)
from .control_plane import WorkflowControlPlane
from .registry import DRAFT_RENEWAL_EMAIL_KIND, ExecutionStrategy


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
        draft_runtimes: DraftExecutionRuntimeFactory,
        worker_id: str,
        application_build: str,
        lease_duration: timedelta = timedelta(minutes=5),
    ) -> None:
        self._control_plane = control_plane
        self._draft_runtimes = draft_runtimes
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
            )
        )
        if packet is None:
            return None
        if (
            packet.job_kind != DRAFT_RENEWAL_EMAIL_KIND
            or packet.execution_strategy != ExecutionStrategy.FRESH_EXECUTION_AGENT.value
            or packet.runtime_instance_id is None
        ):
            raise RuntimeError(f"No V0 executor is installed for {packet.job_kind!r}")

        async with self._draft_runtimes.create(packet.runtime_instance_id) as runtime:
            if runtime.runtime_instance_id != packet.runtime_instance_id:
                raise RuntimeError("Draft runtime identity does not match the claimed Run")
            result = await runtime.execute(packet.input)

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
        async with self._interactions.create() as interaction:
            await interaction.handle(
                packet.notification_id,
                packet.workflow_event_id,
                packet.workflow_id,
            )
        return await self._control_plane.acknowledge_notification(
            AcknowledgeNotificationCommand(
                notification_id=packet.notification_id,
                worker_id=self._worker_id,
            )
        )
