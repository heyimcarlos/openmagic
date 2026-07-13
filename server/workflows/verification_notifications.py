"""Read-only projection of verification Notification delivery state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from .contracts import (
    ProtectedOperation,
    VerificationDeliveryAttention,
    VerificationResumeDelivery,
)
from .database import WorkflowDatabase
from .errors import NotificationLifecycleError
from .models import NotificationRow, VerificationChallengeRow, WorkflowEventRow
from .registry import VERIFICATION_DELIVERY_ATTENTION_NOTIFICATION_KIND

VERIFICATION_RESUME_NOTIFICATION_KIND = "verification_resume_required"
VERIFICATION_RESUME_RECOVERY_NOTIFICATION_KIND = "verification_resume_recovery_required"


class VerificationNotificationResolver:
    """Resolve leased verification Notifications into bounded delivery data."""

    def __init__(
        self,
        *,
        database: WorkflowDatabase,
        clock: Callable[[], datetime],
    ) -> None:
        self._database = database
        self._clock = clock

    async def read_delivery_attention(
        self,
        *,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
        worker_id: str,
        delivery_attempt: int,
    ) -> VerificationDeliveryAttention:
        """Resolve safe recovery copy through a current Notification lease."""

        now = self._clock()
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
                        NotificationRow.kind == VERIFICATION_DELIVERY_ATTENTION_NOTIFICATION_KIND,
                    )
                )
            ).one_or_none()
            if row is None:
                raise NotificationLifecycleError("Verification delivery Notification is invalid")
            notification, event = row
            if (
                notification.status != "delivering"
                or notification.claimed_by != worker_id
                or notification.attempts != delivery_attempt
                or notification.lease_expires_at is None
                or notification.lease_expires_at <= now
                or event.job_id is None
                or event.event_type not in {"run_failed", "run_outcome_uncertain", "run_abandoned"}
            ):
                raise NotificationLifecycleError("Verification delivery lease is stale")
            challenge = await session.scalar(
                sa.select(VerificationChallengeRow).where(
                    VerificationChallengeRow.delivery_workflow_id == workflow_id,
                    VerificationChallengeRow.delivery_job_id == event.job_id,
                )
            )
            if (
                challenge is None
                or notification.destination_type != "interaction"
                or notification.destination_id != challenge.interaction_id
            ):
                raise NotificationLifecycleError("Verification delivery destination is invalid")
            if challenge.status in {"verified", "superseded"}:
                return VerificationDeliveryAttention(
                    interaction_id=challenge.interaction_id,
                    message=None,
                )
            uncertain = event.event_type == "run_outcome_uncertain" or bool(
                event.data.get("dispatch_started")
            )
            message = (
                "I briefly lost confirmation of the verification email delivery. If your "
                "verification already succeeded, no action is needed. Otherwise, I will not "
                "send another automatically. You can use the code if it arrives before it "
                "expires."
                if uncertain
                else "I could not send the verification email. Please try your request again."
            )
            return VerificationDeliveryAttention(
                interaction_id=challenge.interaction_id,
                message=message,
            )

    async def read_resume_delivery(
        self,
        *,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
        worker_id: str,
        delivery_attempt: int,
    ) -> VerificationResumeDelivery:
        """Resolve one exact continuation through a current Notification lease."""

        now = self._clock()
        async with self._database.read_transaction() as session:
            notification = await session.scalar(
                sa.select(NotificationRow).where(
                    NotificationRow.id == notification_id,
                    NotificationRow.workflow_event_id == workflow_event_id,
                    NotificationRow.workflow_id == workflow_id,
                    NotificationRow.kind == VERIFICATION_RESUME_NOTIFICATION_KIND,
                )
            )
            if (
                notification is None
                or notification.status != "delivering"
                or notification.claimed_by != worker_id
                or notification.attempts != delivery_attempt
                or notification.lease_expires_at is None
                or notification.lease_expires_at <= now
            ):
                raise NotificationLifecycleError("Verification resume lease is stale")
            event = await session.scalar(
                sa.select(WorkflowEventRow).where(
                    WorkflowEventRow.id == workflow_event_id,
                    WorkflowEventRow.workflow_id == workflow_id,
                    WorkflowEventRow.event_type == "verification_succeeded",
                )
            )
            if event is None:
                raise NotificationLifecycleError("Verification resume Event is invalid")
            try:
                challenge_id = UUID(str(event.data["challenge_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise NotificationLifecycleError("Verification resume Event is invalid") from exc
            challenge = await session.scalar(
                sa.select(VerificationChallengeRow).where(
                    VerificationChallengeRow.id == challenge_id,
                    VerificationChallengeRow.workflow_id == workflow_id,
                    VerificationChallengeRow.status == "verified",
                )
            )
            if (
                challenge is None
                or notification.destination_type != "interaction"
                or notification.destination_id != challenge.interaction_id
                or event.cause_id != challenge.verified_cause_id
            ):
                raise NotificationLifecycleError("Verification resume Challenge is invalid")
            return VerificationResumeDelivery(
                challenge_id=challenge.id,
                actor_party_id=challenge.actor_party_id,
                interaction_id=challenge.interaction_id,
                workflow_id=challenge.workflow_id,
                request_cause_id=challenge.request_cause_id,
                operation=ProtectedOperation(
                    name=challenge.operation_name,
                    arguments=challenge.operation_arguments,
                ),
            )

    async def read_resume_recovery_destination(
        self,
        *,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
        worker_id: str,
        delivery_attempt: int,
    ) -> str:
        """Resolve one durable terminal-resume recovery Notification."""

        now = self._clock()
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
                        NotificationRow.kind == VERIFICATION_RESUME_RECOVERY_NOTIFICATION_KIND,
                        NotificationRow.status == "delivering",
                        NotificationRow.claimed_by == worker_id,
                        NotificationRow.attempts == delivery_attempt,
                        NotificationRow.lease_expires_at.is_not(None),
                        NotificationRow.lease_expires_at > now,
                        WorkflowEventRow.event_type == "verification_resume_delivery_failed",
                    )
                )
            ).one_or_none()
            if row is None:
                raise NotificationLifecycleError("Verification resume recovery lease is stale")
            notification, _event = row
            if notification.destination_type != "interaction":
                raise NotificationLifecycleError(
                    "Verification resume recovery destination is invalid"
                )
            return notification.destination_id


__all__ = [
    "VERIFICATION_RESUME_NOTIFICATION_KIND",
    "VERIFICATION_RESUME_RECOVERY_NOTIFICATION_KIND",
    "VerificationNotificationResolver",
]
