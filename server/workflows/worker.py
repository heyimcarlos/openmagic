"""Run-scoped execution and Notification delivery coordinators."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from .contracts import (
    AcknowledgeNotificationCommand,
    BeginExternalEffectDispatchCommand,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    NotificationDeliveryPacket,
    ReportNotificationFailureCommand,
    ReportRunResultCommand,
    RunResult,
    WorkflowExecutionPacket,
)
from .control_plane import WorkflowControlPlane
from .email_adapter import EmailAdapterValidationError, EmailSendAdapter
from .email_effects import EmailSendEffectV1
from .errors import WorkflowError


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

    def create(
        self,
        worker_id: str,
        delivery_attempt: int,
    ) -> AbstractAsyncContextManager[NotificationInteraction]: ...


class WorkflowWorker:
    """Claim and execute at most one Job per tick."""

    def __init__(
        self,
        *,
        control_plane: WorkflowControlPlane,
        executors: Mapping[str, DraftExecutionRuntimeFactory],
        email_adapters: Mapping[str, EmailSendAdapter] | None = None,
        worker_id: str,
        application_build: str,
        lease_duration: timedelta = timedelta(minutes=5),
    ) -> None:
        self._control_plane = control_plane
        self._executors = dict(executors)
        self._email_adapters = dict(email_adapters or {})
        if not self._executors and not self._email_adapters:
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
                executor_keys=(*self._executors, *self._email_adapters),
            )
        )
        if packet is None:
            return None
        if packet.execution_strategy == "deterministic_adapter":
            result = await self._execute_email_adapter(packet)
        else:
            result = await self._execute_draft(packet)

        report = ReportRunResultCommand(run_id=packet.run_id, result=result)
        for retry in range(3):
            try:
                await self._control_plane.report_run_result(report)
                break
            except WorkflowError:
                raise
            except Exception:
                if retry == 2:
                    raise
                await asyncio.sleep(0.1 * (retry + 1))
        return packet

    async def _execute_draft(self, packet: WorkflowExecutionPacket) -> RunResult:
        factory = self._executors.get(packet.executor_key)
        if factory is None or packet.runtime_instance_id is None:
            raise RuntimeError(f"No executor is installed for {packet.executor_key!r}")
        try:
            async with factory.create(packet.runtime_instance_id) as runtime:
                if runtime.runtime_instance_id != packet.runtime_instance_id:
                    raise RuntimeError("Runtime identity does not match the claimed Run")
                return await runtime.execute(packet.input)
        except Exception:
            return RunResult(
                outcome="failed",
                evidence=({"type": "executor_failed"},),
                error={"code": "executor_unavailable"},
            )

    async def _execute_email_adapter(self, packet: WorkflowExecutionPacket) -> RunResult:
        adapter = self._email_adapters.get(packet.executor_key)
        if adapter is None:
            raise RuntimeError(f"No adapter is installed for {packet.executor_key!r}")
        effect = EmailSendEffectV1.model_validate(packet.input)
        try:
            adapter.validate_effect(effect)
        except EmailAdapterValidationError as exc:
            return RunResult(
                outcome="failed",
                evidence=({"type": "adapter_validation_failed_before_dispatch"},),
                error={"code": "adapter_configuration_invalid", "detail": str(exc)},
            )
        dispatch = await self._control_plane.begin_external_effect_dispatch(
            BeginExternalEffectDispatchCommand(run_id=packet.run_id)
        )
        if dispatch.effect != effect:
            raise RuntimeError("Dispatch effect changed after claim")
        try:
            return await adapter.send_email(dispatch.effect, dispatch.context)
        except Exception as exc:
            return RunResult(
                outcome="uncertain",
                evidence=({"type": "adapter_outcome_uncertain"},),
                error={"code": "adapter_outcome_uncertain", "detail": type(exc).__name__},
            )


class NotificationWorker:
    """Deliver one Notification, then acknowledge only completed handling."""

    def __init__(
        self,
        *,
        control_plane: WorkflowControlPlane,
        interactions: NotificationInteractionFactory,
        worker_id: str,
        lease_duration: timedelta = timedelta(minutes=5),
        notification_kinds: tuple[str, ...] = (),
        on_delivery_failure: (
            Callable[[NotificationDeliveryPacket], Awaitable[None]] | None
        ) = None,
    ) -> None:
        self._control_plane = control_plane
        self._interactions = interactions
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._notification_kinds = notification_kinds
        self._on_delivery_failure = on_delivery_failure

    async def run_once(self) -> NotificationDeliveryPacket | None:
        packet = await self._control_plane.claim_notification(
            ClaimNotificationCommand(
                worker_id=self._worker_id,
                lease_duration=self._lease_duration,
                kinds=self._notification_kinds,
            )
        )
        if packet is None:
            return None
        try:
            async with self._interactions.create(
                self._worker_id,
                packet.delivery_attempt,
            ) as interaction:
                await interaction.handle(
                    packet.notification_id,
                    packet.workflow_event_id,
                    packet.workflow_id,
                )
        except Exception:
            failed = await self._control_plane.report_notification_failure(
                ReportNotificationFailureCommand(
                    notification_id=packet.notification_id,
                    worker_id=self._worker_id,
                    delivery_attempt=packet.delivery_attempt,
                    error_code="interaction_delivery_failed",
                )
            )
            if self._on_delivery_failure is not None:
                await self._on_delivery_failure(failed)
            raise
        return await self._control_plane.acknowledge_notification(
            AcknowledgeNotificationCommand(
                notification_id=packet.notification_id,
                worker_id=self._worker_id,
                delivery_attempt=packet.delivery_attempt,
            )
        )
