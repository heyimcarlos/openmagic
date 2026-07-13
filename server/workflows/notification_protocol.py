"""Transactional Notification lease and acknowledgement protocol."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .authority import CurrentBrokerAuthority
from .contracts import (
    AcknowledgeNotificationCommand,
    ClaimNotificationCommand,
    NotificationAudienceContext,
    NotificationDeliveryPacket,
    NotificationDeliveryStatus,
    NotificationPresentationContext,
    NotificationStatusContext,
    ReportNotificationFailureCommand,
    WorkflowCommandContext,
)
from .database import WorkflowDatabase
from .email_effects import fingerprint_email_effect, resolve_email_effect
from .errors import NotificationLifecycleError, WorkflowLifecycleError
from .models import (
    NotificationRow,
    WorkflowEventRow,
    WorkflowJobDependencyRow,
    WorkflowJobRow,
    WorkflowRow,
)
from .verification_notifications import (
    VERIFICATION_RESUME_NOTIFICATION_KIND,
    VERIFICATION_RESUME_RECOVERY_NOTIFICATION_KIND,
)


class WorkflowNotificationProtocol:
    """Own Notification delivery state behind the Control Plane facade."""

    def __init__(
        self,
        database: WorkflowDatabase,
        has_current_broker_authority: CurrentBrokerAuthority,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._has_current_broker_authority = has_current_broker_authority
        self._clock = clock or self._utc_now

    async def claim_notification(
        self,
        command: ClaimNotificationCommand,
    ) -> NotificationDeliveryPacket | None:
        now = self._clock()
        async with self._database.transaction() as session:
            await self._recover_expired(session, now)
            filters = [
                NotificationRow.status == "queued",
                NotificationRow.available_at <= now,
                NotificationRow.attempts < NotificationRow.max_attempts,
            ]
            if command.kinds:
                filters.append(NotificationRow.kind.in_(command.kinds))
            notification = await session.scalar(
                sa.select(NotificationRow)
                .where(*filters)
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
            notification.delivered_at = self._clock()
            notification.delivered_by = command.worker_id
            await session.flush()
            return self._packet(notification)

    async def report_failure(
        self,
        command: ReportNotificationFailureCommand,
    ) -> NotificationDeliveryPacket:
        now = self._clock()
        async with self._database.transaction() as session:
            workflow_id = await session.scalar(
                sa.select(NotificationRow.workflow_id).where(
                    NotificationRow.id == command.notification_id
                )
            )
            if workflow_id is None:
                raise NotificationLifecycleError("Notification does not exist")
            workflow = await session.scalar(
                sa.select(WorkflowRow).where(WorkflowRow.id == workflow_id).with_for_update()
            )
            if workflow is None:
                raise NotificationLifecycleError("Notification Workflow does not exist")
            notification = await session.scalar(
                sa.select(NotificationRow)
                .where(
                    NotificationRow.id == command.notification_id,
                    NotificationRow.workflow_id == workflow.id,
                )
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
                notification.available_at = now + self._backoff(notification.attempts)
            else:
                await self._fail_terminally(session, notification, now)
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
                    workflow,
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
            effect = await resolve_email_effect(session, workflow_id, send)
            fingerprint = fingerprint_email_effect(effect)
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
                        "sender_mailbox_id": str(effect.sender_mailbox_id),
                    },
                )
            )
            await session.flush()
            return NotificationPresentationContext(
                destination_party_id=destination_party_id,
                draft_job_id=draft.id,
                send_job_id=send.id,
                effect_fingerprint=fingerprint,
                effect=effect.model_dump(mode="json"),
            )

    async def resolve_audience(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
        worker_id: str,
        delivery_attempt: int,
    ) -> NotificationAudienceContext:
        async with self._database.read_transaction() as session:
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
        expected_event = {
            "approval_required": "draft_ready",
            "send_confirmed": "email_send_succeeded",
        }.get(notification.kind)
        if (
            notification.destination_type != "party"
            or expected_event is None
            or event.event_type != expected_event
        ):
            raise NotificationLifecycleError("Notification is not deliverable")
        try:
            destination_party_id = UUID(notification.destination_id)
        except ValueError as exc:
            raise NotificationLifecycleError("Notification destination is invalid") from exc
        return NotificationAudienceContext(
            destination_party_id=destination_party_id,
            kind=notification.kind,
        )

    async def resolve_status(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
        worker_id: str,
        delivery_attempt: int,
    ) -> NotificationStatusContext:
        audience = await self.resolve_audience(
            notification_id,
            workflow_event_id,
            workflow_id,
            worker_id,
            delivery_attempt,
        )
        if audience.kind != "send_confirmed":
            raise NotificationLifecycleError("Notification is not a send confirmation")
        async with self._database.transaction() as session:
            workflow = await session.scalar(
                sa.select(WorkflowRow).where(WorkflowRow.id == workflow_id).with_for_update()
            )
            notification = await session.scalar(
                sa.select(NotificationRow).where(NotificationRow.id == notification_id)
            )
            event = await session.scalar(
                sa.select(WorkflowEventRow).where(
                    WorkflowEventRow.workflow_id == workflow_id,
                    WorkflowEventRow.id == workflow_event_id,
                )
            )
            if workflow is None or notification is None or event is None:
                raise NotificationLifecycleError("Notification aggregate does not exist")
            self._require_current_lease(notification, worker_id, delivery_attempt)
            if workflow.status != "completed" or event.job_id is None:
                raise NotificationLifecycleError("Workflow send is not completed")
            job = await session.scalar(
                sa.select(WorkflowJobRow).where(
                    WorkflowJobRow.workflow_id == workflow_id,
                    WorkflowJobRow.id == event.job_id,
                    WorkflowJobRow.status == "succeeded",
                )
            )
            if job is None or event.event_type != "email_send_succeeded":
                raise NotificationLifecycleError("Send confirmation evidence is invalid")
            authority_context = WorkflowCommandContext(
                actor_party_id=audience.destination_party_id,
                organization_party_id=workflow.organization_party_id,
                cause_type="message",
                cause_id=f"notification:{notification_id}",
            )
            if not await self._has_current_broker_authority(
                session,
                authority_context,
                workflow,
            ):
                raise NotificationLifecycleError(
                    "Notification destination no longer has Broker authority"
                )
            return NotificationStatusContext(
                destination_party_id=audience.destination_party_id,
                message="The renewal email was sent successfully.",
            )

    @classmethod
    async def _committed_presentation(
        cls,
        session: AsyncSession,
        event: WorkflowEventRow,
        destination_party_id: UUID,
        workflow: WorkflowRow,
    ) -> NotificationPresentationContext:
        if workflow.status != "active":
            raise NotificationLifecycleError("Committed presentation is no longer actionable")
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
                WorkflowJobRow.status == "waiting",
            )
        )
        if send is None:
            raise NotificationLifecycleError("Committed presentation is no longer actionable")
        draft = await session.scalar(
            sa.select(WorkflowJobRow).where(
                WorkflowJobRow.workflow_id == event.workflow_id,
                WorkflowJobRow.id == draft_job_id,
                WorkflowJobRow.status == "succeeded",
            )
        )
        approval_exists = await session.scalar(
            sa.select(WorkflowEventRow.id)
            .where(
                WorkflowEventRow.workflow_id == event.workflow_id,
                WorkflowEventRow.job_id == send.id,
                WorkflowEventRow.event_type == "approval_granted",
            )
            .limit(1)
        )
        if (
            draft is None
            or approval_exists is not None
            or not await cls._dependencies_succeeded(session, event.workflow_id, send.id)
        ):
            raise NotificationLifecycleError("Committed presentation is no longer actionable")
        sender_mailbox_id = event.data.get("sender_mailbox_id")
        try:
            effect = await resolve_email_effect(
                session,
                event.workflow_id,
                send,
                sender_mailbox_id=UUID(str(sender_mailbox_id)) if sender_mailbox_id else None,
            )
        except WorkflowLifecycleError as exc:
            raise NotificationLifecycleError(
                "Committed presentation is no longer actionable"
            ) from exc
        if fingerprint_email_effect(effect) != fingerprint:
            raise NotificationLifecycleError("Committed presentation fingerprint is invalid")
        return NotificationPresentationContext(
            destination_party_id=destination_party_id,
            draft_job_id=draft_job_id,
            send_job_id=send.id,
            effect_fingerprint=fingerprint,
            effect=effect.model_dump(mode="json"),
        )

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

    async def _recover_expired(self, session: AsyncSession, now: datetime) -> None:
        expired = (
            await session.execute(
                sa.select(NotificationRow.id, NotificationRow.workflow_id)
                .where(
                    NotificationRow.status == "delivering",
                    NotificationRow.lease_expires_at <= now,
                )
                .order_by(
                    NotificationRow.workflow_id,
                    NotificationRow.lease_expires_at,
                    NotificationRow.id,
                )
                .limit(20)
            )
        ).all()
        for notification_id, workflow_id in expired:
            workflow = await session.scalar(
                sa.select(WorkflowRow).where(WorkflowRow.id == workflow_id).with_for_update()
            )
            if workflow is None:
                continue
            notification = await session.scalar(
                sa.select(NotificationRow)
                .where(
                    NotificationRow.id == notification_id,
                    NotificationRow.workflow_id == workflow.id,
                    NotificationRow.status == "delivering",
                    NotificationRow.lease_expires_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
            if notification is None:
                continue
            notification.claimed_by = None
            notification.lease_expires_at = None
            notification.last_error = "delivery_lease_expired"
            if notification.attempts < notification.max_attempts:
                notification.status = "queued"
                notification.available_at = now + WorkflowNotificationProtocol._backoff(
                    notification.attempts
                )
            else:
                await self._fail_terminally(session, notification, now)

    @staticmethod
    async def _fail_terminally(
        session: AsyncSession,
        notification: NotificationRow,
        now: datetime,
    ) -> None:
        notification.status = "failed"
        if notification.kind != VERIFICATION_RESUME_NOTIFICATION_KIND:
            return
        event = WorkflowEventRow(
            id=uuid4(),
            workflow_id=notification.workflow_id,
            event_type="verification_resume_delivery_failed",
            actor_type="system",
            actor_id="notification_control_plane",
            cause_type="notification",
            cause_id=str(notification.id),
            data={
                "notification_id": str(notification.id),
                "notification_kind": notification.kind,
                "error": notification.last_error,
            },
            occurred_at=now,
        )
        session.add(event)
        await session.flush()
        session.add(
            NotificationRow(
                workflow_id=notification.workflow_id,
                workflow_event_id=event.id,
                kind=VERIFICATION_RESUME_RECOVERY_NOTIFICATION_KIND,
                destination_type=notification.destination_type,
                destination_id=notification.destination_id,
                status="queued",
                attempts=0,
                max_attempts=3,
                available_at=now,
            )
        )

    def _require_current_lease(
        self,
        notification: NotificationRow,
        worker_id: str,
        delivery_attempt: int,
    ) -> None:
        if (
            notification.status != "delivering"
            or notification.claimed_by != worker_id
            or notification.attempts != delivery_attempt
            or notification.lease_expires_at is None
            or notification.lease_expires_at <= self._clock()
        ):
            raise NotificationLifecycleError("Notification delivery lease is stale")

    @staticmethod
    def _packet(notification: NotificationRow) -> NotificationDeliveryPacket:
        return NotificationDeliveryPacket(
            notification_id=notification.id,
            workflow_event_id=notification.workflow_event_id,
            workflow_id=notification.workflow_id,
            kind=notification.kind,
            delivery_attempt=notification.attempts,
            status=cast(NotificationDeliveryStatus, notification.status),
        )

    @staticmethod
    def _backoff(attempt: int) -> timedelta:
        return timedelta(seconds=min(2 ** max(attempt - 1, 0), 30))

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)
