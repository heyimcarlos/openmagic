"""Secret-safe AgentMail recipient evidence for live Workflow acceptance."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from email.utils import getaddresses
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from server.workflows import EmailSendEffectV1


class _Inbox(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    inbox_id: str
    email: EmailStr


class _InboxPage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    inboxes: tuple[_Inbox, ...]


class _MessageSummary(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    message_id: str
    subject: str


class _MessagePage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    messages: tuple[_MessageSummary, ...]


class AgentMailMessageDetail(BaseModel):
    """Fields independently observed from one received message."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    message_id: str
    sender: str = Field(alias="from")
    to: tuple[str, ...]
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    subject: str
    text: str
    extracted_text: str
    html: str | None = None

    @field_validator("cc", "bcc", mode="before")
    @classmethod
    def empty_recipient_list_for_null(cls, value: object) -> object:
        return () if value is None else value


class AgentMailRecipient:
    """Observe one authorized disposable inbox without exposing its contents."""

    def __init__(self, api_key: SecretStr, recipient: EmailStr) -> None:
        self._recipient = recipient
        self._client = httpx.AsyncClient(
            base_url="https://api.agentmail.to/v0",
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            timeout=20,
        )

    async def __aenter__(self) -> AgentMailRecipient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self._client.aclose()

    async def message_ids(self) -> set[str]:
        return {message.message_id for message in await self._message_summaries()}

    async def wait_for_exactly_one_message(
        self,
        *,
        effect: EmailSendEffectV1,
        previous_ids: set[str],
        wait_limit: timedelta,
        settlement_period: timedelta = timedelta(seconds=6),
    ) -> bool:
        first_match_at: float | None = None
        try:
            async with asyncio.timeout(wait_limit.total_seconds()):
                while True:
                    messages = await self._correlated_messages(effect, previous_ids)
                    now = asyncio.get_running_loop().time()
                    if self.contains_exactly_one_effect(messages, effect):
                        first_match_at = first_match_at or now
                        if now - first_match_at >= settlement_period.total_seconds():
                            return True
                    elif len(messages) > 1:
                        return False
                    await asyncio.sleep(2)
        except TimeoutError:
            return False

    async def wait_for_one_new_subject(
        self,
        *,
        subject: str,
        previous_ids: set[str],
        wait_limit: timedelta,
        settlement_period: timedelta = timedelta(seconds=6),
    ) -> AgentMailMessageDetail:
        """Return one newly received message without logging its content."""

        candidate_id: str | None = None
        first_match_at: float | None = None
        try:
            async with asyncio.timeout(wait_limit.total_seconds()):
                while True:
                    candidates = [
                        message
                        for message in await self._message_summaries()
                        if message.message_id not in previous_ids and message.subject == subject
                    ]
                    if len(candidates) == 1:
                        now = asyncio.get_running_loop().time()
                        if candidate_id != candidates[0].message_id:
                            candidate_id = candidates[0].message_id
                            first_match_at = now
                        if (
                            first_match_at is not None
                            and now - first_match_at >= settlement_period.total_seconds()
                        ):
                            return await self._message_detail(candidates[0].message_id)
                    if len(candidates) > 1:
                        raise RuntimeError("More than one correlated AgentMail message arrived")
                    await asyncio.sleep(2)
        except TimeoutError:
            raise RuntimeError("Correlated AgentMail message did not arrive") from None

    @classmethod
    def contains_exactly_one_effect(
        cls,
        messages: tuple[AgentMailMessageDetail, ...],
        effect: EmailSendEffectV1,
    ) -> bool:
        return len(messages) == 1 and cls._matches_effect(messages[0], effect)

    @staticmethod
    def _matches_effect(
        message: AgentMailMessageDetail,
        effect: EmailSendEffectV1,
    ) -> bool:
        return (
            _addresses((message.sender,)) == (str(effect.expected_sender_address).lower(),)
            and _addresses(message.to) == tuple(str(value).lower() for value in effect.to)
            and _addresses(message.cc) == tuple(str(value).lower() for value in effect.cc)
            and _addresses(message.bcc) == tuple(str(value).lower() for value in effect.bcc)
            and message.subject == effect.subject
            and message.text.rstrip("\r\n") == effect.body
            and message.extracted_text == effect.body
            and message.html in {None, ""}
        )

    async def _correlated_messages(
        self,
        effect: EmailSendEffectV1,
        previous_ids: set[str],
    ) -> tuple[AgentMailMessageDetail, ...]:
        candidates = [
            message
            for message in await self._message_summaries()
            if message.message_id not in previous_ids and message.subject == effect.subject
        ]
        details: list[AgentMailMessageDetail] = []
        for message in candidates:
            details.append(await self._message_detail(message.message_id))
        return tuple(details)

    async def _message_summaries(self) -> tuple[_MessageSummary, ...]:
        inbox_id = await self._inbox_id()
        try:
            response = await self._client.get(f"/inboxes/{quote(inbox_id, safe='')}/messages")
            response.raise_for_status()
            return _MessagePage.model_validate(response.json()).messages
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("AgentMail message response is unavailable or malformed") from None

    async def _message_detail(self, message_id: str) -> AgentMailMessageDetail:
        inbox_id = await self._inbox_id()
        try:
            response = await self._client.get(
                f"/inboxes/{quote(inbox_id, safe='')}/messages/{quote(message_id, safe='')}"
            )
            response.raise_for_status()
            return AgentMailMessageDetail.model_validate(response.json())
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("AgentMail message detail is unavailable or malformed") from None

    async def _inbox_id(self) -> str:
        try:
            response = await self._client.get("/inboxes")
            response.raise_for_status()
            inboxes = _InboxPage.model_validate(response.json()).inboxes
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("AgentMail inbox response is unavailable or malformed") from None
        inbox = next((item for item in inboxes if item.email == self._recipient), None)
        if inbox is None:
            raise RuntimeError("Configured AgentMail recipient does not exist")
        return inbox.inbox_id


def _addresses(values: tuple[str, ...]) -> tuple[str, ...]:
    parsed = tuple(address.lower() for _name, address in getaddresses(values) if address)
    if len(parsed) != len(values):
        return ()
    return parsed


__all__ = ["AgentMailMessageDetail", "AgentMailRecipient"]
