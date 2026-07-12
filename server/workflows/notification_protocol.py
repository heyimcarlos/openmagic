"""Transactional Notification lease and acknowledgement protocol."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import (
    AcknowledgeNotificationCommand,
    ClaimNotificationCommand,
    NotificationDeliveryPacket,
    NotificationPresentationContext,
    ReportNotificationFailureCommand,
    WorkflowCommandContext,
)
from .database import WorkflowDatabase
from .errors import NotificationLifecycleError
from .models import (
    NotificationRow,
    WorkflowEventRow,
    WorkflowJobDependencyRow,
    WorkflowJobRow,
    WorkflowRow,
)

CurrentBrokerAuthority = Callable[
    [AsyncSession, WorkflowCommandContext, WorkflowRow], Awaitable[bool]
]


class WorkflowNotificationProtocol:
    """Own Notification delivery state behind the Control Plane facade."""

    def __init__(
        self,
        database: WorkflowDatabase,
        has_current_broker_authority: CurrentBrokerAuthority,
    ) -> None:
        self._database = database
        self._has_current_broker_authority = has_current_broker_authority

    async def claim_notification(
        self,
        command: ClaimNotificationCommand,
    ) -> NotificationDeliveryPacket | None:
        now = datetime.now(UTC)
        async with self._database.transaction() as session:
            await self._recover_expired(session, now)
            notification = await session.scalar(
                sa.select(NotificationRow)
                .where(
                    NotificationRow.status == "queued",
                    NotificationRow.available_at <= now,
                    NotificationRow.attempts < NotificationRow.max_attempts,
                )
                .order_by(
                    NotificationRow.available_at,
                    NotificationRow.created_at,
                    NotificationRow.id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if notification is None:
                return None
            notification.status = "delivering"
            notification.attempts += 1
            notification.claimed_by = command.worker_id
            notification.lease_expires_at = now + command.lease_duration
            await session.flush()
            return self._packet(notification)

    async def acknowledge_notification(
        self,
        command: AcknowledgeNotificationCommand,
    ) -> NotificationDeliveryPacket:
        async with self._database.transaction() as session:
            notification = await session.scalar(
                sa.select(NotificationRow)
                .where(NotificationRow.id == command.notification_id)
                .with_for_update()
            )
            if notification is None:
                raise NotificationLifecycleError("Notification does not exist")
            if notification.status == "delivered":
                if (
                    notification.attempts != command.delivery_attempt
                    or notification.delivered_by != command.worker_id
                ):
                    raise NotificationLifecycleError("Notification acknowledgement is stale")
                return self._packet(notification)
            self._require_current_lease(
                notification,
                command.worker_id,
                command.delivery_attempt,
            )
            notification.status = "delivered"
            notification.claimed_by = None
            notification.lease_expires_at = None
            notification.delivered_at = datetime.now(UTC)
            notification.delivered_by = command.worker_id
            await session.flush()
            return self._packet(notification)

    async def report_failure(
        self,
        command: ReportNotificationFailureCommand,
    ) -> NotificationDeliveryPacket:
        async with self._database.transaction() as session:
            notification = await session.scalar(
                sa.select(NotificationRow)
                .where(NotificationRow.id == command.notification_id)
                .with_for_update()
            )
            if notification is None:
                raise NotificationLifecycleError("Notification does not exist")
            self._require_current_lease(
                notification,
                command.worker_id,
                command.delivery_attempt,
            )
            notification.claimed_by = None
            notification.lease_expires_at = None
            notification.last_error = command.error_code
            if notification.attempts < notification.max_attempts:
                notification.status = "queued"
                notification.available_at = datetime.now(UTC) + self._backoff(notification.attempts)
            else:
                notification.status = "failed"
            await session.flush()
            return self._packet(notification)

    async def resolve_presentation(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
        worker_id: str,
        delivery_attempt: int,
    ) -> NotificationPresentationContext:
        async with self._database.transaction() as session:
            workflow = await session.scalar(
                sa.select(WorkflowRow).where(WorkflowRow.id == workflow_id).with_for_update()
            )
            if workflow is None:
                raise NotificationLifecycleError("Workflow does not exist")
            row = (
                await session.execute(
                    sa.select(NotificationRow, WorkflowEventRow)
                    .join(
                        WorkflowEventRow,
                        sa.and_(
                            WorkflowEventRow.workflow_id == NotificationRow.workflow_id,
                            WorkflowEventRow.id == NotificationRow.workflow_event_id,
                        ),
                    )
                    .where(
                        NotificationRow.id == notification_id,
                        NotificationRow.workflow_event_id == workflow_event_id,
                        NotificationRow.workflow_id == workflow_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise NotificationLifecycleError("Notification identifiers do not match")
            notification, event = row
            self._require_current_lease(notification, worker_id, delivery_attempt)
            if (
                notification.kind != "approval_required"
                or notification.destination_type != "party"
                or event.event_type != "draft_ready"
                or event.job_id is None
            ):
                raise NotificationLifecycleError("Notification is not deliverable for approval")
            try:
                destination_party_id = UUID(notification.destination_id)
            except ValueError as exc:
                raise NotificationLifecycleError("Notification destination is invalid") from exc

            committed = await session.scalar(
                sa.select(WorkflowEventRow).where(
                    WorkflowEventRow.workflow_id == workflow_id,
                    WorkflowEventRow.event_type == "approval_presentation_committed",
                    WorkflowEventRow.cause_type == "notification",
                    WorkflowEventRow.cause_id == str(notification_id),
                )
            )
            if committed is not None:
                return await self._committed_presentation(
                    session,
                    committed,
                    destination_party_id,
                )
            authorized = await self._has_current_broker_authority(
                session,
                WorkflowCommandContext(
                    actor_party_id=destination_party_id,
                    organization_party_id=workflow.organization_party_id,
                    cause_type="message",
                    cause_id=f"notification:{notification_id}",
                ),
                workflow,
            )
            if not authorized:
                raise NotificationLifecycleError(
                    "Notification destination no longer has Broker authority"
                )

            draft = await session.scalar(
                sa.select(WorkflowJobRow).where(
                    WorkflowJobRow.workflow_id == workflow_id,
                    WorkflowJobRow.id == event.job_id,
                    WorkflowJobRow.status == "succeeded",
                )
            )
            candidates = (
                await session.scalars(
                    sa.select(WorkflowJobRow)
                    .join(
                        WorkflowJobDependencyRow,
                        sa.and_(
                            WorkflowJobDependencyRow.workflow_id == WorkflowJobRow.workflow_id,
                            WorkflowJobDependencyRow.job_id == WorkflowJobRow.id,
                        ),
                    )
                    .where(
                        WorkflowJobRow.workflow_id == workflow_id,
                        WorkflowJobRow.kind == "gmail.send_email.v1",
                        WorkflowJobRow.status == "waiting",
                        WorkflowJobDependencyRow.depends_on_job_id == event.job_id,
                        ~sa.exists(
                            sa.select(WorkflowEventRow.id).where(
                                WorkflowEventRow.workflow_id == workflow_id,
                                WorkflowEventRow.job_id == WorkflowJobRow.id,
                                WorkflowEventRow.event_type == "approval_granted",
                            )
                        ),
                    )
                    .order_by(WorkflowJobRow.created_at, WorkflowJobRow.id)
                )
            ).all()
            eligible = [
                job
                for job in candidates
                if await self._dependencies_succeeded(session, workflow_id, job.id)
            ]
            if draft is None or len(eligible) != 1:
                raise NotificationLifecycleError(
                    "Notification does not identify one current Send Job awaiting approval"
                )
            send = eligible[0]
            effect = await self._resolve_effect(session, workflow_id, send)
            fingerprint = self._fingerprint(effect)
            session.add(
                WorkflowEventRow(
                    workflow_id=workflow_id,
                    job_id=send.id,
                    event_type="approval_presentation_committed",
                    actor_type="worker",
                    actor_id=worker_id,
                    cause_type="notification",
                    cause_id=str(notification_id),
                    data={
                        "draft_job_id": str(draft.id),
                        "effect_fingerprint": fingerprint,
                    },
                )
            )
            await session.flush()
            return NotificationPresentationContext(
                destination_party_id=destination_party_id,
                draft_job_id=draft.id,
                send_job_id=send.id,
                effect_fingerprint=fingerprint,
                effect=effect,
            )

    @classmethod
    async def _committed_presentation(
        cls,
        session: AsyncSession,
        event: WorkflowEventRow,
        destination_party_id: UUID,
    ) -> NotificationPresentationContext:
        if event.job_id is None:
            raise NotificationLifecycleError("Committed presentation has no Send Job")
        try:
            draft_job_id = UUID(str(event.data["draft_job_id"]))
            fingerprint = str(event.data["effect_fingerprint"])
        except (KeyError, TypeError, ValueError) as exc:
            raise NotificationLifecycleError("Committed presentation is invalid") from exc
        send = await session.scalar(
            sa.select(WorkflowJobRow).where(
                WorkflowJobRow.workflow_id == event.workflow_id,
                WorkflowJobRow.id == event.job_id,
            )
        )
        if send is None:
            raise NotificationLifecycleError("Committed presentation Send Job is missing")
        effect = await cls._resolve_effect(session, event.workflow_id, send)
        if cls._fingerprint(effect) != fingerprint:
            raise NotificationLifecycleError("Committed presentation fingerprint is invalid")
        return NotificationPresentationContext(
            destination_party_id=destination_party_id,
            draft_job_id=draft_job_id,
            send_job_id=send.id,
            effect_fingerprint=fingerprint,
            effect=effect,
        )

    @staticmethod
    async def _resolve_effect(
        session: AsyncSession,
        workflow_id: UUID,
        send: WorkflowJobRow,
    ) -> dict[str, object]:
        resolved = dict(send.input)
        for field, value in send.input.items():
            if not isinstance(value, dict) or set(value) != {"job_output", "field"}:
                continue
            try:
                source_id = UUID(str(value["job_output"]))
                source_field = str(value["field"])
            except (KeyError, TypeError, ValueError) as exc:
                raise NotificationLifecycleError("Send Job input reference is invalid") from exc
            source = await session.scalar(
                sa.select(WorkflowJobRow).where(
                    WorkflowJobRow.workflow_id == workflow_id,
                    WorkflowJobRow.id == source_id,
                )
            )
            if source is None or source.output is None or source_field not in source.output:
                raise NotificationLifecycleError("Send Job input reference is unresolved")
            resolved[field] = source.output[source_field]
        return resolved

    @staticmethod
    def _fingerprint(effect: dict[str, object]) -> str:
        encoded = json.dumps(effect, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    async def _dependencies_succeeded(
        session: AsyncSession,
        workflow_id: UUID,
        job_id: UUID,
    ) -> bool:
        unresolved = await session.scalar(
            sa.select(WorkflowJobDependencyRow.job_id)
            .join(
                WorkflowJobRow,
                sa.and_(
                    WorkflowJobRow.workflow_id == WorkflowJobDependencyRow.workflow_id,
                    WorkflowJobRow.id == WorkflowJobDependencyRow.depends_on_job_id,
                ),
            )
            .where(
                WorkflowJobDependencyRow.workflow_id == workflow_id,
                WorkflowJobDependencyRow.job_id == job_id,
                WorkflowJobRow.status != "succeeded",
            )
            .limit(1)
        )
        return unresolved is None

    @staticmethod
    async def _recover_expired(session: AsyncSession, now: datetime) -> None:
        expired = (
            await session.scalars(
                sa.select(NotificationRow)
                .where(
                    NotificationRow.status == "delivering",
                    NotificationRow.lease_expires_at < now,
                )
                .order_by(NotificationRow.lease_expires_at, NotificationRow.id)
                .with_for_update(skip_locked=True)
                .limit(20)
            )
        ).all()
        for notification in expired:
            notification.claimed_by = None
            notification.lease_expires_at = None
            notification.last_error = "delivery_lease_expired"
            if notification.attempts < notification.max_attempts:
                notification.status = "queued"
                notification.available_at = now + WorkflowNotificationProtocol._backoff(
                    notification.attempts
                )
            else:
                notification.status = "failed"

    @staticmethod
    def _require_current_lease(
        notification: NotificationRow,
        worker_id: str,
        delivery_attempt: int,
    ) -> None:
        if (
            notification.status != "delivering"
            or notification.claimed_by != worker_id
            or notification.attempts != delivery_attempt
            or notification.lease_expires_at is None
            or notification.lease_expires_at < datetime.now(UTC)
        ):
            raise NotificationLifecycleError("Notification delivery lease is stale")

    @staticmethod
    def _packet(notification: NotificationRow) -> NotificationDeliveryPacket:
        return NotificationDeliveryPacket(
            notification_id=notification.id,
            workflow_event_id=notification.workflow_event_id,
            workflow_id=notification.workflow_id,
            delivery_attempt=notification.attempts,
        )

    @staticmethod
    def _backoff(attempt: int) -> timedelta:
        return timedelta(seconds=min(2 ** max(attempt - 1, 0), 30))
