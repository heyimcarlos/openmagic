"""Typed External Effect adapter for verification email delivery."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol
from uuid import UUID

from .contracts import RunResult, VerificationEmailDelivery, WorkflowExecutionPacket
from .email_adapter import (
    COMPOSIO_GMAIL_TOOL,
    COMPOSIO_GMAIL_TOOLKIT_VERSION,
    DuplicateEmailSendError,
    normalize_composio_gmail_response,
)
from .errors import WorkflowError
from .verification import StepUpVerification


class VerificationEmailSender(Protocol):
    """Replaceable provider call after the durable dispatch boundary."""

    async def send(self, delivery: VerificationEmailDelivery) -> RunResult: ...


class DeterministicVerificationEmailSender:
    """Inspectable fake that follows the live adapter result contract."""

    def __init__(
        self,
        result: RunResult | None = None,
        *,
        invocation_error: Exception | None = None,
    ) -> None:
        self._result = result or RunResult(
            outcome="succeeded",
            data={
                "provider": "deterministic_verification_email",
                "acknowledged": True,
                "tool_version": "fake.v1",
                "message_id": "verification-message",
                "thread_id": None,
            },
            evidence=({"type": "deterministic_provider_acknowledgement"},),
        )
        self._invocation_error = invocation_error
        self._deliveries: list[VerificationEmailDelivery] = []
        self._invoked_job_ids: set[UUID] = set()

    @property
    def deliveries(self) -> tuple[VerificationEmailDelivery, ...]:
        return tuple(self._deliveries)

    async def send(self, delivery: VerificationEmailDelivery) -> RunResult:
        if delivery.job_id in self._invoked_job_ids:
            raise DuplicateEmailSendError(
                f"Verification email Job {delivery.job_id} was already invoked"
            )
        self._invoked_job_ids.add(delivery.job_id)
        self._deliveries.append(delivery)
        if self._invocation_error is not None:
            raise self._invocation_error
        return self._result


class ComposioVerificationEmailSender:
    """Send one code through pinned, retry-disabled Composio Gmail execution."""

    def __init__(self, *, client: Any, composio_user_id: str) -> None:
        self._client = client
        self._composio_user_id = composio_user_id
        self._invoked_job_ids: set[UUID] = set()

    async def send(self, delivery: VerificationEmailDelivery) -> RunResult:
        if delivery.job_id in self._invoked_job_ids:
            raise DuplicateEmailSendError(
                f"Verification email Job {delivery.job_id} was already invoked"
            )
        self._invoked_job_ids.add(delivery.job_id)
        try:
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
                        "It expires in 10 minutes."
                    ),
                    "is_html": False,
                },
            )
        except Exception as exc:
            return RunResult(
                outcome="uncertain",
                evidence=(
                    {
                        "type": "provider_outcome_uncertain",
                        "provider": "composio_gmail",
                        "tool_version": COMPOSIO_GMAIL_TOOLKIT_VERSION,
                    },
                ),
                error={"code": "provider_communication_lost", "detail": type(exc).__name__},
            )
        return normalize_composio_gmail_response(response)


class VerificationEmailExecutionHandler:
    """Execute one claimed verification email Job through normal Run evidence."""

    def __init__(
        self,
        *,
        verification: StepUpVerification,
        sender: VerificationEmailSender,
    ) -> None:
        self._verification = verification
        self._sender = sender

    async def execute(self, packet: WorkflowExecutionPacket) -> RunResult:
        try:
            delivery = await self._verification.begin_email_dispatch(run_id=packet.run_id)
        except WorkflowError as exc:
            return RunResult(
                outcome="failed",
                evidence=({"type": "verification_delivery_rejected_before_provider_call"},),
                error={"code": "verification_delivery_rejected", "detail": str(exc)},
            )
        try:
            return await self._sender.send(delivery)
        except Exception as exc:
            return RunResult(
                outcome="uncertain",
                evidence=({"type": "verification_email_outcome_uncertain"},),
                error={
                    "code": "verification_email_outcome_uncertain",
                    "detail": type(exc).__name__,
                },
            )


__all__ = [
    "ComposioVerificationEmailSender",
    "DeterministicVerificationEmailSender",
    "VerificationEmailExecutionHandler",
    "VerificationEmailSender",
]
