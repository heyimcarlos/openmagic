"""Fresh Interaction Agent delivery for durable verified continuations."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from server.config import Settings
from server.services.conversation import get_conversation_session
from server.workflows import StepUpVerification


class VerificationResumeInteraction:
    """Resolve and execute one exact continuation without prior prompt context."""

    def __init__(
        self,
        *,
        verification: StepUpVerification,
        settings: Settings,
        worker_id: str,
        delivery_attempt: int,
    ) -> None:
        self._verification = verification
        self._settings = settings
        self._worker_id = worker_id
        self._delivery_attempt = delivery_attempt

    async def handle(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
    ) -> None:
        delivery = await self._verification.read_resume_delivery(
            notification_id=notification_id,
            workflow_event_id=workflow_event_id,
            workflow_id=workflow_id,
            worker_id=self._worker_id,
            delivery_attempt=self._delivery_attempt,
        )
        session = get_conversation_session(delivery.interaction_id)
        from .factory import create_interaction_runtime

        runtime = create_interaction_runtime(
            self._settings,
            actor_party_id=delivery.actor_party_id,
            interaction_id=delivery.interaction_id,
            conversation_state=session.log,
            working_memory_state=session.working_memory,
        )
        result = await runtime.execute_verified_resume(
            notification_id=notification_id,
            operation_cause_id=delivery.request_cause_id,
            operation_cause_type=delivery.request_cause_type,
            challenge_id=delivery.challenge_id,
            workflow_id=delivery.workflow_id,
            operation=delivery.operation,
        )
        if not result.response:
            raise RuntimeError("Verified continuation produced no user-facing response")


class VerificationResumeInteractionFactory:
    """Create one bounded, history-free verified continuation delivery."""

    def __init__(
        self,
        *,
        verification: StepUpVerification,
        settings: Settings,
    ) -> None:
        self._verification = verification
        self._settings = settings

    @asynccontextmanager
    async def create(
        self,
        worker_id: str,
        delivery_attempt: int,
    ) -> AsyncIterator[VerificationResumeInteraction]:
        yield VerificationResumeInteraction(
            verification=self._verification,
            settings=self._settings,
            worker_id=worker_id,
            delivery_attempt=delivery_attempt,
        )


class VerificationResumeRecoveryInteraction:
    """Deliver deterministic recovery after resume presentation exhausts retries."""

    MESSAGE = (
        "If you already received the result, no action is needed. Otherwise, your identity "
        "verification succeeded, but I could not confirm result delivery. Please ask me to "
        "try the protected request again."
    )

    def __init__(
        self,
        *,
        verification: StepUpVerification,
        worker_id: str,
        delivery_attempt: int,
    ) -> None:
        self._verification = verification
        self._worker_id = worker_id
        self._delivery_attempt = delivery_attempt

    async def handle(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
    ) -> None:
        interaction_id = await self._verification.read_resume_recovery_destination(
            notification_id=notification_id,
            workflow_event_id=workflow_event_id,
            workflow_id=workflow_id,
            worker_id=self._worker_id,
            delivery_attempt=self._delivery_attempt,
        )
        session = get_conversation_session(interaction_id)
        session.log.record_reply_once(
            str(notification_id),
            self.MESSAGE,
        )


class VerificationResumeRecoveryInteractionFactory:
    """Create one deterministic terminal-resume recovery delivery."""

    def __init__(self, *, verification: StepUpVerification) -> None:
        self._verification = verification

    @asynccontextmanager
    async def create(
        self,
        worker_id: str,
        delivery_attempt: int,
    ) -> AsyncIterator[VerificationResumeRecoveryInteraction]:
        yield VerificationResumeRecoveryInteraction(
            verification=self._verification,
            worker_id=worker_id,
            delivery_attempt=delivery_attempt,
        )


class VerificationDeliveryAttentionInteraction:
    """Deliver deterministic recovery copy without invoking a model."""

    def __init__(
        self,
        *,
        verification: StepUpVerification,
        worker_id: str,
        delivery_attempt: int,
    ) -> None:
        self._verification = verification
        self._worker_id = worker_id
        self._delivery_attempt = delivery_attempt

    async def handle(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
    ) -> None:
        delivery = await self._verification.read_delivery_attention(
            notification_id=notification_id,
            workflow_event_id=workflow_event_id,
            workflow_id=workflow_id,
            worker_id=self._worker_id,
            delivery_attempt=self._delivery_attempt,
        )
        if delivery.message is not None:
            session = get_conversation_session(delivery.interaction_id)
            session.log.record_reply_once(str(notification_id), delivery.message)


class VerificationDeliveryAttentionInteractionFactory:
    """Create one bounded deterministic verification-recovery delivery."""

    def __init__(self, *, verification: StepUpVerification) -> None:
        self._verification = verification

    @asynccontextmanager
    async def create(
        self,
        worker_id: str,
        delivery_attempt: int,
    ) -> AsyncIterator[VerificationDeliveryAttentionInteraction]:
        yield VerificationDeliveryAttentionInteraction(
            verification=self._verification,
            worker_id=worker_id,
            delivery_attempt=delivery_attempt,
        )


__all__ = [
    "VerificationDeliveryAttentionInteractionFactory",
    "VerificationResumeInteractionFactory",
    "VerificationResumeRecoveryInteractionFactory",
]
