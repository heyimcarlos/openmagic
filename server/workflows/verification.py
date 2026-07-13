"""Durable step-up verification for protected Workflow operations."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import (
    AuthorizeProtectedOperationCommand,
    ProtectedOperation,
    SubmitVerificationCodeCommand,
    VerificationCodeResult,
    VerificationDecision,
    VerificationEmailDelivery,
)
from .database import WorkflowDatabase
from .errors import NotificationLifecycleError
from .identity_models import PartyIdentifierRow, WorkflowParticipantRow
from .models import (
    InteractionCauseRow,
    NotificationRow,
    VerificationChallengeRow,
    WorkflowEventRow,
)

VERIFICATION_EMAIL_NOTIFICATION_KIND = "verification_code_email"


class StepUpVerification:
    """Authorize protected operations through one durable cross-channel challenge."""

    def __init__(
        self,
        *,
        database: WorkflowDatabase,
        code_secret: bytes,
        clock: Callable[[], datetime] | None = None,
        challenge_ttl: timedelta = timedelta(minutes=15),
        authorization_ttl: timedelta = timedelta(minutes=15),
        delivery_available: bool = True,
    ) -> None:
        if len(code_secret) < 16:
            raise ValueError("Verification code secret must contain at least 16 bytes")
        self._database = database
        self._code_secret = code_secret
        self._clock = clock or self._utc_now
        self._challenge_ttl = challenge_ttl
        self._authorization_ttl = authorization_ttl
        self._delivery_available = delivery_available

    async def authorize_or_challenge(
        self,
        command: AuthorizeProtectedOperationCommand,
    ) -> VerificationDecision:
        """Return fresh authorization or atomically queue one email challenge."""

        now = self._clock()
        fingerprint = self._operation_fingerprint(command.operation)
        async with self._database.transaction() as session:
            await self._lock_interaction(
                session,
                command.actor_party_id,
                command.interaction_id,
            )
            authorization = await session.scalar(
                sa.select(VerificationChallengeRow)
                .where(
                    VerificationChallengeRow.actor_party_id == command.actor_party_id,
                    VerificationChallengeRow.interaction_id == command.interaction_id,
                    VerificationChallengeRow.workflow_id == command.workflow_id,
                    VerificationChallengeRow.purpose == command.purpose,
                    VerificationChallengeRow.status == "verified",
                    VerificationChallengeRow.authorization_expires_at > now,
                    sa.exists().where(
                        PartyIdentifierRow.id == VerificationChallengeRow.destination_identifier_id,
                        PartyIdentifierRow.party_id == command.actor_party_id,
                        PartyIdentifierRow.kind == "email",
                        PartyIdentifierRow.verified_at.is_not(None),
                        PartyIdentifierRow.revoked_at.is_(None),
                    ),
                )
                .order_by(
                    VerificationChallengeRow.authorization_expires_at.desc(),
                    VerificationChallengeRow.id,
                )
                .limit(1)
            )
            if authorization is not None:
                return VerificationDecision(
                    status="authorized",
                    challenge_id=authorization.id,
                    authorization_expires_at=authorization.authorization_expires_at,
                )

            pending = await session.scalar(
                sa.select(VerificationChallengeRow)
                .where(
                    VerificationChallengeRow.actor_party_id == command.actor_party_id,
                    VerificationChallengeRow.interaction_id == command.interaction_id,
                    VerificationChallengeRow.status == "pending",
                )
                .with_for_update()
                .limit(1)
            )
            if pending is not None and pending.expires_at <= now:
                pending.status = "expired"
                self._add_terminal_event(
                    session,
                    pending,
                    command.cause_id,
                    "verification_expired",
                    now,
                )
                await self._fail_queued_delivery(
                    session,
                    pending.created_event_id,
                    "verification_challenge_expired",
                )
                pending = None
            if pending is not None:
                same_request = (
                    pending.workflow_id == command.workflow_id
                    and pending.purpose == command.purpose
                    and pending.operation_fingerprint == fingerprint
                )
                if same_request:
                    delivery_status = await session.scalar(
                        sa.select(NotificationRow.status).where(
                            NotificationRow.workflow_event_id == pending.created_event_id,
                            NotificationRow.kind == VERIFICATION_EMAIL_NOTIFICATION_KIND,
                        )
                    )
                    if delivery_status == "failed":
                        pending.status = "superseded"
                        self._add_terminal_event(
                            session,
                            pending,
                            command.cause_id,
                            "verification_delivery_failed",
                            now,
                        )
                        pending = None
                    elif not self._delivery_available and delivery_status != "delivered":
                        pending.status = "failed"
                        self._add_terminal_event(
                            session,
                            pending,
                            command.cause_id,
                            "verification_delivery_failed",
                            now,
                        )
                        await self._fail_queued_delivery(
                            session,
                            pending.created_event_id,
                            "verification_delivery_unavailable",
                        )
                        return VerificationDecision(status="verification_unavailable")
                    else:
                        destination = await session.get(
                            PartyIdentifierRow,
                            pending.destination_identifier_id,
                        )
                        return VerificationDecision(
                            status="verification_required",
                            challenge_id=pending.id,
                            masked_destination=(
                                self._mask_email(destination.value)
                                if destination is not None
                                else None
                            ),
                            expires_at=pending.expires_at,
                        )
                if pending is not None:
                    pending.status = "superseded"
                    self._add_terminal_event(
                        session,
                        pending,
                        command.cause_id,
                        "verification_superseded",
                        now,
                    )
                    await self._fail_queued_delivery(
                        session,
                        pending.created_event_id,
                        "verification_challenge_superseded",
                    )

            if not self._delivery_available:
                return VerificationDecision(status="verification_unavailable")

            participant = await session.scalar(
                sa.select(WorkflowParticipantRow.party_id).where(
                    WorkflowParticipantRow.workflow_id == command.workflow_id,
                    WorkflowParticipantRow.party_id == command.actor_party_id,
                )
            )
            identifiers = (
                await session.scalars(
                    sa.select(PartyIdentifierRow)
                    .where(
                        PartyIdentifierRow.party_id == command.actor_party_id,
                        PartyIdentifierRow.kind == "email",
                        PartyIdentifierRow.verified_at.is_not(None),
                        PartyIdentifierRow.revoked_at.is_(None),
                    )
                    .order_by(PartyIdentifierRow.created_at, PartyIdentifierRow.id)
                )
            ).all()
            if participant is None or len(identifiers) != 1:
                return VerificationDecision(status="verification_unavailable")

            destination = identifiers[0]
            challenge_id = uuid4()
            event_id = uuid4()
            expires_at = now + self._challenge_ttl
            event = WorkflowEventRow(
                id=event_id,
                workflow_id=command.workflow_id,
                event_type="verification_challenge_created",
                actor_type="party",
                actor_id=str(command.actor_party_id),
                cause_type="message",
                cause_id=command.cause_id,
                data={
                    "challenge_id": str(challenge_id),
                    "interaction_id": command.interaction_id,
                    "purpose": command.purpose,
                    "operation": command.operation.name,
                    "destination": self._mask_email(destination.value),
                    "expires_at": expires_at.isoformat(),
                },
                occurred_at=now,
            )
            session.add(event)
            await session.flush()
            session.add_all(
                (
                    VerificationChallengeRow(
                        id=challenge_id,
                        actor_party_id=command.actor_party_id,
                        interaction_id=command.interaction_id,
                        workflow_id=command.workflow_id,
                        purpose=command.purpose,
                        operation_name=command.operation.name,
                        operation_arguments=command.operation.arguments,
                        operation_fingerprint=fingerprint,
                        request_cause_id=command.cause_id,
                        destination_identifier_id=destination.id,
                        created_event_id=event_id,
                        status="pending",
                        expires_at=expires_at,
                        failed_attempts=0,
                        max_attempts=5,
                        created_at=now,
                    ),
                    NotificationRow(
                        id=uuid4(),
                        workflow_id=command.workflow_id,
                        workflow_event_id=event_id,
                        kind=VERIFICATION_EMAIL_NOTIFICATION_KIND,
                        destination_type="email",
                        destination_id=str(destination.id),
                        status="queued",
                        attempts=0,
                        max_attempts=3,
                        available_at=now,
                        created_at=now,
                    ),
                )
            )
            return VerificationDecision(
                status="verification_required",
                challenge_id=challenge_id,
                masked_destination=self._mask_email(destination.value),
                expires_at=expires_at,
            )

    async def read_email_delivery(
        self,
        *,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
        worker_id: str,
        delivery_attempt: int,
    ) -> VerificationEmailDelivery:
        """Resolve secret delivery material only for a currently leased Notification."""

        now = self._clock()
        async with self._database.read_transaction() as session:
            notification = await session.scalar(
                sa.select(NotificationRow).where(
                    NotificationRow.id == notification_id,
                    NotificationRow.workflow_event_id == workflow_event_id,
                    NotificationRow.workflow_id == workflow_id,
                    NotificationRow.kind == VERIFICATION_EMAIL_NOTIFICATION_KIND,
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
                raise NotificationLifecycleError("Verification delivery lease is stale")
            challenge = await session.scalar(
                sa.select(VerificationChallengeRow).where(
                    VerificationChallengeRow.created_event_id == workflow_event_id,
                    VerificationChallengeRow.workflow_id == workflow_id,
                )
            )
            if challenge is None or challenge.status != "pending" or challenge.expires_at <= now:
                raise NotificationLifecycleError("Verification challenge is no longer deliverable")
            destination = await session.get(
                PartyIdentifierRow,
                challenge.destination_identifier_id,
            )
            if (
                destination is None
                or destination.kind != "email"
                or destination.party_id != challenge.actor_party_id
                or destination.verified_at is None
                or destination.revoked_at is not None
            ):
                raise NotificationLifecycleError("Verification destination is no longer valid")
            return VerificationEmailDelivery(
                challenge_id=challenge.id,
                destination=destination.value,
                code=self._code(challenge.id),
                expires_at=challenge.expires_at,
            )

    async def record_terminal_delivery_failure(
        self,
        *,
        notification_id: UUID,
        workflow_event_id: UUID,
        workflow_id: UUID,
    ) -> str | None:
        """Fail a pending challenge after its delivery retries are exhausted."""

        now = self._clock()
        async with self._database.transaction() as session:
            notification = await session.scalar(
                sa.select(NotificationRow).where(
                    NotificationRow.id == notification_id,
                    NotificationRow.workflow_event_id == workflow_event_id,
                    NotificationRow.workflow_id == workflow_id,
                    NotificationRow.kind == VERIFICATION_EMAIL_NOTIFICATION_KIND,
                    NotificationRow.status == "failed",
                )
            )
            if notification is None:
                return None
            challenge = await session.scalar(
                sa.select(VerificationChallengeRow)
                .where(
                    VerificationChallengeRow.created_event_id == workflow_event_id,
                    VerificationChallengeRow.status == "pending",
                )
                .with_for_update()
            )
            if challenge is None:
                return None
            challenge.status = "failed"
            session.add(
                WorkflowEventRow(
                    id=uuid4(),
                    workflow_id=challenge.workflow_id,
                    event_type="verification_delivery_failed",
                    actor_type="worker",
                    actor_id="verification-email-worker",
                    cause_type="notification",
                    cause_id=str(notification_id),
                    data={
                        "challenge_id": str(challenge.id),
                        "interaction_id": challenge.interaction_id,
                        "purpose": challenge.purpose,
                        "authorization_expires_at": None,
                    },
                    occurred_at=now,
                )
            )
            return challenge.interaction_id

    async def submit_code(
        self,
        command: SubmitVerificationCodeCommand,
    ) -> VerificationCodeResult:
        """Atomically consume one active code and return its exact waiting operation."""

        now = self._clock()
        async with self._database.transaction() as session:
            content_digest = hashlib.sha256(command.code.encode()).hexdigest()
            interaction_cause = await session.get(InteractionCauseRow, command.cause_id)
            if interaction_cause is None:
                session.add(
                    InteractionCauseRow(
                        id=command.cause_id,
                        cause_type="message",
                        actor_party_id=command.actor_party_id,
                        content_digest=content_digest,
                        occurred_at=now,
                    )
                )
                await session.flush()
            elif (
                interaction_cause.actor_party_id != command.actor_party_id
                or interaction_cause.content_digest != content_digest
            ):
                raise ValueError("Verification Cause ID conflicts with an earlier interaction")
            replay = await session.scalar(
                sa.select(VerificationChallengeRow).where(
                    VerificationChallengeRow.actor_party_id == command.actor_party_id,
                    VerificationChallengeRow.interaction_id == command.interaction_id,
                    VerificationChallengeRow.status == "verified",
                    VerificationChallengeRow.verified_cause_id == command.cause_id,
                )
            )
            if replay is not None:
                return self._verified_result(replay)

            challenge = await session.scalar(
                sa.select(VerificationChallengeRow)
                .where(
                    VerificationChallengeRow.actor_party_id == command.actor_party_id,
                    VerificationChallengeRow.interaction_id == command.interaction_id,
                    VerificationChallengeRow.status == "pending",
                )
                .with_for_update()
                .limit(1)
            )
            if challenge is None:
                return VerificationCodeResult(status="no_active_challenge")
            if challenge.expires_at <= now:
                challenge.status = "expired"
                await self._fail_queued_delivery(
                    session,
                    challenge.created_event_id,
                    "verification_challenge_expired",
                )
                self._add_terminal_event(
                    session,
                    challenge,
                    command.cause_id,
                    "verification_expired",
                    now,
                )
                return VerificationCodeResult(
                    status="expired",
                    challenge_id=challenge.id,
                    workflow_id=challenge.workflow_id,
                    purpose=cast(Literal["sensitive_read", "sensitive_write"], challenge.purpose),
                )
            if not hmac.compare_digest(command.code, self._code(challenge.id)):
                challenge.failed_attempts += 1
                if challenge.failed_attempts >= challenge.max_attempts:
                    challenge.status = "failed"
                    self._add_terminal_event(
                        session,
                        challenge,
                        command.cause_id,
                        "verification_failed",
                        now,
                    )
                    return VerificationCodeResult(
                        status="attempts_exhausted",
                        challenge_id=challenge.id,
                        workflow_id=challenge.workflow_id,
                        purpose=cast(
                            Literal["sensitive_read", "sensitive_write"], challenge.purpose
                        ),
                    )
                return VerificationCodeResult(
                    status="invalid_code",
                    challenge_id=challenge.id,
                    workflow_id=challenge.workflow_id,
                    purpose=cast(Literal["sensitive_read", "sensitive_write"], challenge.purpose),
                )

            destination = await session.get(
                PartyIdentifierRow,
                challenge.destination_identifier_id,
            )
            if (
                destination is None
                or destination.party_id != challenge.actor_party_id
                or destination.kind != "email"
                or destination.verified_at is None
                or destination.revoked_at is not None
            ):
                challenge.status = "failed"
                await self._fail_queued_delivery(
                    session,
                    challenge.created_event_id,
                    "verification_destination_invalid",
                )
                self._add_terminal_event(
                    session,
                    challenge,
                    command.cause_id,
                    "verification_failed",
                    now,
                )
                return VerificationCodeResult(
                    status="verification_unavailable",
                    challenge_id=challenge.id,
                    workflow_id=challenge.workflow_id,
                    purpose=cast(Literal["sensitive_read", "sensitive_write"], challenge.purpose),
                )

            challenge.status = "verified"
            challenge.verified_at = now
            challenge.verified_cause_id = command.cause_id
            challenge.authorization_expires_at = now + self._authorization_ttl
            self._add_terminal_event(
                session,
                challenge,
                command.cause_id,
                "verification_succeeded",
                now,
            )
            return self._verified_result(challenge)

    def _add_terminal_event(
        self,
        session: AsyncSession,
        challenge: VerificationChallengeRow,
        cause_id: str,
        event_type: str,
        now: datetime,
    ) -> None:
        session.add(
            WorkflowEventRow(
                id=uuid4(),
                workflow_id=challenge.workflow_id,
                event_type=event_type,
                actor_type="party",
                actor_id=str(challenge.actor_party_id),
                cause_type="message",
                cause_id=cause_id,
                data={
                    "challenge_id": str(challenge.id),
                    "interaction_id": challenge.interaction_id,
                    "purpose": challenge.purpose,
                    "authorization_expires_at": (
                        challenge.authorization_expires_at.isoformat()
                        if challenge.authorization_expires_at is not None
                        else None
                    ),
                },
                occurred_at=now,
            )
        )

    @staticmethod
    async def _fail_queued_delivery(
        session: AsyncSession,
        event_id: UUID,
        error: str,
    ) -> None:
        await session.execute(
            sa.update(NotificationRow)
            .where(
                NotificationRow.workflow_event_id == event_id,
                NotificationRow.kind == VERIFICATION_EMAIL_NOTIFICATION_KIND,
                NotificationRow.status == "queued",
            )
            .values(status="failed", last_error=error)
        )

    @staticmethod
    async def _lock_interaction(
        session: AsyncSession,
        actor_party_id: UUID,
        interaction_id: str,
    ) -> None:
        material = f"{actor_party_id}:{interaction_id}".encode()
        lock_key = int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=True)
        await session.execute(sa.select(sa.func.pg_advisory_xact_lock(lock_key)))

    @staticmethod
    def _operation_fingerprint(operation: ProtectedOperation) -> str:
        encoded = json.dumps(
            operation.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _code(self, challenge_id: UUID) -> str:
        digest = hmac.new(self._code_secret, challenge_id.bytes, hashlib.sha256).digest()
        return f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"

    @staticmethod
    def _mask_email(address: str) -> str:
        local, separator, domain = address.partition("@")
        if not separator:
            return "***"
        return f"{local[:1]}***@{domain}"

    @staticmethod
    def _verified_result(challenge: VerificationChallengeRow) -> VerificationCodeResult:
        return VerificationCodeResult(
            status="verified",
            challenge_id=challenge.id,
            workflow_id=challenge.workflow_id,
            purpose=cast(Literal["sensitive_read", "sensitive_write"], challenge.purpose),
            request_cause_id=challenge.request_cause_id,
            operation=ProtectedOperation(
                name=challenge.operation_name,
                arguments=challenge.operation_arguments,
            ),
            authorization_expires_at=challenge.authorization_expires_at,
        )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)


__all__ = ["VERIFICATION_EMAIL_NOTIFICATION_KIND", "StepUpVerification"]
