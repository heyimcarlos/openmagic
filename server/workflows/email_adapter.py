"""Deterministic and live adapters for one approved Gmail External Effect."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr

from .contracts import RunResult
from .email_effects import EmailSendEffectV1, EmailSendExecutionContextV1

COMPOSIO_GMAIL_TOOL = "GMAIL_SEND_EMAIL"
COMPOSIO_GMAIL_TOOLKIT_VERSION = "20260702_01"


class DuplicateEmailSendError(RuntimeError):
    """The same logical email Job reached an adapter more than once."""


class EmailAdapterValidationError(ValueError):
    """Trusted adapter configuration rejected the effect before dispatch."""


class EmailSendAdapter(Protocol):
    """One side-effecting operation behind the deterministic Worker boundary."""

    def validate_effect(self, effect: EmailSendEffectV1) -> None: ...

    async def send_email(
        self,
        effect: EmailSendEffectV1,
        context: EmailSendExecutionContextV1,
    ) -> RunResult: ...


class ComposioMailboxBinding(BaseModel):
    """Trusted application mapping from OpenMagic mailbox to Composio identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sender_mailbox_id: UUID
    expected_sender_address: EmailStr
    composio_user_id: str


class _InvokeOnce:
    def __init__(self) -> None:
        self._invoked_job_ids: set[UUID] = set()

    def _begin(self, job_id: UUID) -> None:
        if job_id in self._invoked_job_ids:
            raise DuplicateEmailSendError(f"Email Job {job_id} was already invoked")
        self._invoked_job_ids.add(job_id)


class DeterministicEmailSendAdapter(_InvokeOnce):
    """Stateful inspectable fake that satisfies the live adapter contract."""

    def __init__(
        self,
        result: RunResult | None = None,
        *,
        pre_dispatch_error: str | None = None,
        invocation_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self._result = result or _successful_result({})
        self._pre_dispatch_error = pre_dispatch_error
        self._invocation_error = invocation_error
        self._invocations: list[tuple[EmailSendEffectV1, EmailSendExecutionContextV1]] = []

    @property
    def invocations(
        self,
    ) -> tuple[tuple[EmailSendEffectV1, EmailSendExecutionContextV1], ...]:
        return tuple(self._invocations)

    def validate_effect(self, effect: EmailSendEffectV1) -> None:
        EmailSendEffectV1.model_validate(effect)
        if self._pre_dispatch_error:
            raise EmailAdapterValidationError(self._pre_dispatch_error)

    async def send_email(
        self,
        effect: EmailSendEffectV1,
        context: EmailSendExecutionContextV1,
    ) -> RunResult:
        self.validate_effect(effect)
        self._begin(context.job_id)
        self._invocations.append((effect, context))
        if self._invocation_error is not None:
            raise self._invocation_error
        return self._result


class ComposioGmailSendAdapter(_InvokeOnce):
    """Pinned, retry-disabled Composio Gmail send through the public SDK path."""

    def __init__(
        self,
        *,
        client: Any,
        binding: ComposioMailboxBinding,
    ) -> None:
        super().__init__()
        self._client = client
        self._binding = binding
        self._execute_call_count = 0

    @property
    def execute_call_count(self) -> int:
        """Number of calls made to Composio's public tool-execution seam."""

        return self._execute_call_count

    def validate_effect(self, effect: EmailSendEffectV1) -> None:
        if (
            effect.sender_mailbox_id != self._binding.sender_mailbox_id
            or effect.expected_sender_address != self._binding.expected_sender_address
        ):
            raise EmailAdapterValidationError(
                "Email effect does not match the configured Composio mailbox"
            )

    async def send_email(
        self,
        effect: EmailSendEffectV1,
        context: EmailSendExecutionContextV1,
    ) -> RunResult:
        self.validate_effect(effect)
        self._begin(context.job_id)
        arguments = {
            "user_id": "me",
            "recipient_email": str(effect.to[0]),
            "extra_recipients": [str(value) for value in effect.to[1:]],
            "cc": [str(value) for value in effect.cc],
            "bcc": [str(value) for value in effect.bcc],
            "subject": effect.subject,
            "body": effect.body,
            "is_html": False,
        }
        try:
            self._execute_call_count += 1
            response = await asyncio.to_thread(
                self._client.tools.execute,
                slug=COMPOSIO_GMAIL_TOOL,
                user_id=self._binding.composio_user_id,
                version=COMPOSIO_GMAIL_TOOLKIT_VERSION,
                arguments=arguments,
            )
        except Exception as exc:
            return _uncertain_result("provider_communication_lost", type(exc).__name__)
        return normalize_composio_gmail_response(response)


def normalize_composio_gmail_response(response: object) -> RunResult:
    """Normalize one pinned Composio Gmail response into the common Run envelope."""

    if not isinstance(response, Mapping):
        return _uncertain_result("malformed_provider_response", type(response).__name__)
    successful = response.get("successful")
    error = response.get("error")
    data = response.get("data")
    if successful is True and (error is None or error == "") and isinstance(data, Mapping):
        return _successful_result(cast(Mapping[str, object], data))
    return _uncertain_result("ambiguous_provider_response", type(error).__name__)


def _successful_result(data: Mapping[str, object]) -> RunResult:
    message_id = data.get("message_id") or data.get("id")
    thread_id = data.get("thread_id") or data.get("threadId")
    return RunResult(
        outcome="succeeded",
        data={
            "provider": "composio_gmail",
            "acknowledged": True,
            "tool_version": COMPOSIO_GMAIL_TOOLKIT_VERSION,
            "message_id": str(message_id) if message_id else None,
            "thread_id": str(thread_id) if thread_id else None,
        },
        evidence=(
            {
                "type": "provider_acknowledgement",
                "provider": "composio_gmail",
                "tool_version": COMPOSIO_GMAIL_TOOLKIT_VERSION,
            },
        ),
    )


def _uncertain_result(code: str, detail: str) -> RunResult:
    return RunResult(
        outcome="uncertain",
        evidence=(
            {
                "type": "provider_outcome_uncertain",
                "provider": "composio_gmail",
                "tool_version": COMPOSIO_GMAIL_TOOLKIT_VERSION,
            },
        ),
        error={"code": code, "detail": detail},
    )


__all__ = [
    "COMPOSIO_GMAIL_TOOL",
    "COMPOSIO_GMAIL_TOOLKIT_VERSION",
    "ComposioGmailSendAdapter",
    "ComposioMailboxBinding",
    "DeterministicEmailSendAdapter",
    "DuplicateEmailSendError",
    "EmailAdapterValidationError",
    "EmailSendAdapter",
    "normalize_composio_gmail_response",
]
