"""Email delivery adapter for durable verification Notifications."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any, Protocol
from uuid import UUID

from .contracts import NotificationDeliveryPacket, VerificationEmailDelivery
from .email_adapter import COMPOSIO_GMAIL_TOOL, COMPOSIO_GMAIL_TOOLKIT_VERSION
from .verification import StepUpVerification


class VerificationEmailSender(Protocol):
    """Replaceable delivery method for one verification challenge."""

    async def send(self, delivery: VerificationEmailDelivery) -> None: ...


class DeterministicVerificationEmailSender:
    """Inspectable fake that records verification email deliveries."""

    def __init__(self) -> None:
        self._deliveries: list[VerificationEmailDelivery] = []

    @property
    def deliveries(self) -> tuple[VerificationEmailDelivery, ...]:
        return tuple(self._deliveries)

    async def send(self, delivery: VerificationEmailDelivery) -> None:
        self._deliveries.append(delivery)


class ComposioVerificationEmailSender:
    """Send verification codes through the configured Composio Gmail identity."""

    def __init__(self, *, client: Any, composio_user_id: str) -> None:
        self._client = client
        self._composio_user_id = composio_user_id

    async def send(self, delivery: VerificationEmailDelivery) -> None:
        response = await asyncio.to_thread(
            self._client.tools.execute,
            slug=COMPOSIO_GMAIL_TOOL,
            user_id=self._composio_user_id,
            version=COMPOSIO_GMAIL_TOOLKIT_VERSION,
            arguments={
                "user_id": "me",
                "recipient_email": delivery.destination,
                "subject": "Your OpenMagic verification code",
                "body": (
                    f"Your OpenMagic verification code is {delivery.code}. "
                    "It expires in 15 minutes."
                ),
                "is_html": False,
            },
        )
        if not isinstance(response, Mapping) or response.get("successful") is not True:
            raise RuntimeError("Verification email provider did not acknowledge delivery")


class VerificationEmailInteraction:
    """Adapt one leased Notification into an email delivery."""

    def __init__(
        self,
        *,
        verification: StepUpVerification,
        sender: VerificationEmailSender,
        worker_id: str,
        delivery_attempt: int,
    ) -> None:
        self._verification = verification
        self._sender = sender
        self._worker_id = worker_id
        self._delivery_attempt = delivery_attempt

    async def handle(
        self,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
    ) -> None:
        delivery = await self._verification.read_email_delivery(
            notification_id=notification_id,
            workflow_event_id=workflow_event_id,
            workflow_id=workflow_id,
            worker_id=self._worker_id,
            delivery_attempt=self._delivery_attempt,
        )
        await self._sender.send(delivery)


class VerificationEmailInteractionFactory:
    """Create one bounded verification email delivery per Notification attempt."""

    def __init__(
        self,
        *,
        verification: StepUpVerification,
        sender: VerificationEmailSender,
    ) -> None:
        self._verification = verification
        self._sender = sender

    @asynccontextmanager
    async def create(
        self,
        worker_id: str,
        delivery_attempt: int,
    ) -> AsyncIterator[VerificationEmailInteraction]:
        yield VerificationEmailInteraction(
            verification=self._verification,
            sender=self._sender,
            worker_id=worker_id,
            delivery_attempt=delivery_attempt,
        )


class VerificationDeliveryFailureHandler:
    """Surface exhausted email delivery retries to the waiting interaction."""

    MESSAGE = (
        "I couldn't deliver the verification email. Please ask me to try the protected "
        "request again."
    )

    def __init__(
        self,
        *,
        verification: StepUpVerification,
        notify: Callable[[str, str], None],
    ) -> None:
        self._verification = verification
        self._notify = notify

    async def __call__(self, packet: NotificationDeliveryPacket) -> None:
        interaction_id = await self._verification.record_terminal_delivery_failure(
            notification_id=packet.notification_id,
            workflow_event_id=packet.workflow_event_id,
            workflow_id=packet.workflow_id,
        )
        if interaction_id is not None:
            self._notify(interaction_id, self.MESSAGE)


__all__ = [
    "ComposioVerificationEmailSender",
    "DeterministicVerificationEmailSender",
    "VerificationDeliveryFailureHandler",
    "VerificationEmailInteractionFactory",
    "VerificationEmailSender",
]
