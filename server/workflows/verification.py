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

from .authority import has_current_workflow_access
from .completion import WorkflowCompletionEvaluator
from .contracts import (
    AuthorizeProtectedOperationCommand,
    ProtectedOperation,
    RunResult,
    SubmitVerificationCodeCommand,
    VerificationCodeResult,
    VerificationDecision,
    VerificationDeliveryAttention,
    VerificationEmailDelivery,
    VerificationResumeDelivery,
)
from .database import WorkflowDatabase
from .errors import StaleRunError, WorkflowLifecycleError
from .identity_models import PartyIdentifierRow
from .models import (
    InteractionCauseRow,
    NotificationRow,
    VerificationChallengeRow,
    WorkflowEventRow,
    WorkflowJobRow,
    WorkflowJobRunRow,
    WorkflowRow,
)
from .registry import (
    VERIFICATION_EMAIL_DELIVERY_WORKFLOW_KIND,
    VERIFICATION_EMAIL_JOB_KIND,
    WorkflowKindRegistry,
    default_workflow_registry,
)
from .verification_notifications import (
    VERIFICATION_RESUME_NOTIFICATION_KIND,
    VerificationNotificationResolver,
)

VerificationCodeStatus = Literal[
    "verified",
    "invalid_code",
    "attempts_exhausted",
    "expired",
    "no_active_challenge",
    "verification_unavailable",
]


class StepUpVerification:
    """Gate protected operations through one durable cross-channel challenge."""

    def __init__(
        self,
        *,
        database: WorkflowDatabase,
        code_secret: bytes,
        registry: WorkflowKindRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
        challenge_ttl: timedelta = timedelta(minutes=10),
        session_ttl: timedelta = timedelta(minutes=15),
        delivery_available: bool = True,
        max_challenges_per_hour: int = 5,
    ) -> None:
        if len(code_secret) < 16:
            raise ValueError("Verification code secret must contain at least 16 bytes")
        self._database = database
        self._code_secret = code_secret
        self._registry = registry or default_workflow_registry()
        self._completion = WorkflowCompletionEvaluator(self._registry)
        self._clock = clock or self._utc_now
        self._notifications = VerificationNotificationResolver(
            database=database,
            clock=self._clock,
        )
        self._challenge_ttl = challenge_ttl
        self._session_ttl = session_ttl
        self._delivery_available = delivery_available
        if max_challenges_per_hour < 1:
            raise ValueError("max_challenges_per_hour must be positive")
        self._max_challenges_per_hour = max_challenges_per_hour

    async def authorize_or_challenge(
        self,
        command: AuthorizeProtectedOperationCommand,
    ) -> VerificationDecision:
        """Reuse fresh identity proof or atomically create one email delivery Job."""

        now = self._clock()
        fingerprint = self._operation_fingerprint(command.operation)
        async with self._database.transaction() as session:
            await self._lock_interaction(session, command.actor_party_id, command.interaction_id)
            pending_locator = (
                await session.execute(
                    sa.select(
                        VerificationChallengeRow.id,
                        VerificationChallengeRow.workflow_id,
                        VerificationChallengeRow.delivery_workflow_id,
                    ).where(
                        VerificationChallengeRow.actor_party_id == command.actor_party_id,
                        VerificationChallengeRow.interaction_id == command.interaction_id,
                        VerificationChallengeRow.status == "pending",
                    )
                )
            ).one_or_none()
            workflow_ids = {command.workflow_id}
            if pending_locator is not None:
                workflow_ids.update(
                    (pending_locator.workflow_id, pending_locator.delivery_workflow_id)
                )
            workflows = await self._lock_workflows(session, workflow_ids)
            workflow = workflows.get(command.workflow_id)
            if (
                workflow is None
                or not self._workflow_supports_purpose(workflow, command.purpose)
                or not await self._has_current_workflow_access(
                    session,
                    workflow,
                    command.actor_party_id,
                )
            ):
                return VerificationDecision(status="verification_unavailable")

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
            if len(identifiers) != 1:
                return VerificationDecision(status="verification_unavailable")
            destination = identifiers[0]
            await self._lock_verification_destination(
                session,
                command.actor_party_id,
                destination.id,
            )

            verification_session = await session.scalar(
                sa.select(VerificationChallengeRow)
                .where(
                    VerificationChallengeRow.actor_party_id == command.actor_party_id,
                    VerificationChallengeRow.interaction_id == command.interaction_id,
                    VerificationChallengeRow.status == "verified",
                    VerificationChallengeRow.verification_session_expires_at > now,
                    sa.exists().where(
                        PartyIdentifierRow.id == VerificationChallengeRow.destination_identifier_id,
                        PartyIdentifierRow.party_id == command.actor_party_id,
                        PartyIdentifierRow.kind == "email",
                        PartyIdentifierRow.verified_at.is_not(None),
                        PartyIdentifierRow.revoked_at.is_(None),
                    ),
                )
                .order_by(
                    VerificationChallengeRow.verification_session_expires_at.desc(),
                    VerificationChallengeRow.id,
                )
                .limit(1)
            )
            if verification_session is not None:
                return VerificationDecision(
                    status="session_valid",
                    challenge_id=verification_session.id,
                    verification_session_expires_at=(
                        verification_session.verification_session_expires_at
                    ),
                )

            pending = None
            if pending_locator is not None:
                pending = await session.scalar(
                    sa.select(VerificationChallengeRow)
                    .where(
                        VerificationChallengeRow.id == pending_locator.id,
                        VerificationChallengeRow.status == "pending",
                    )
                    .with_for_update()
                )
            if pending is not None and pending.expires_at <= now:
                pending.status = "expired"
                self._add_challenge_event(
                    session,
                    pending,
                    command.cause_id,
                    "verification_expired",
                    now,
                    actor_type="system",
                    actor_id="verification_control_plane",
                )
                await self._cancel_delivery_before_dispatch(session, pending, now)
                pending = None
            if pending is not None:
                delivery_job = await session.scalar(
                    sa.select(WorkflowJobRow)
                    .where(
                        WorkflowJobRow.workflow_id == pending.delivery_workflow_id,
                        WorkflowJobRow.id == pending.delivery_job_id,
                    )
                    .with_for_update()
                )
                same_request = (
                    pending.workflow_id == command.workflow_id
                    and pending.purpose == command.purpose
                    and pending.operation_fingerprint == fingerprint
                )
                if delivery_job is None:
                    raise WorkflowLifecycleError("Verification delivery Job is missing")
                if same_request and delivery_job.status not in {"failed", "cancelled"}:
                    return VerificationDecision(
                        status="verification_required",
                        challenge_id=pending.id,
                        masked_destination=self._mask_email(destination.value),
                        expires_at=pending.expires_at,
                    )
                if same_request and delivery_job.status in {"failed", "cancelled"}:
                    await self._cancel_delivery_before_dispatch(session, pending, now)
                    pending.status = "superseded"
                    self._add_challenge_event(
                        session,
                        pending,
                        command.cause_id,
                        "verification_delivery_failed",
                        now,
                    )
                    pending = None
                if pending is not None:
                    if await self._challenge_limit_reached(
                        session,
                        command.actor_party_id,
                        destination.id,
                        now,
                    ):
                        return VerificationDecision(
                            status="verification_in_progress",
                            challenge_id=pending.id,
                            masked_destination=self._mask_email(destination.value),
                            expires_at=pending.expires_at,
                        )
                    if not await self._cancel_delivery_before_dispatch(session, pending, now):
                        return VerificationDecision(
                            status="verification_in_progress",
                            challenge_id=pending.id,
                            masked_destination=self._mask_email(destination.value),
                            expires_at=pending.expires_at,
                        )
                    pending.status = "superseded"
                    self._add_challenge_event(
                        session,
                        pending,
                        command.cause_id,
                        "verification_superseded",
                        now,
                    )

            if not self._delivery_available:
                return VerificationDecision(status="verification_unavailable")
            if await self._challenge_limit_reached(
                session,
                command.actor_party_id,
                destination.id,
                now,
            ):
                return VerificationDecision(status="verification_unavailable")

            challenge_id = uuid4()
            delivery_workflow_id = uuid4()
            delivery_job_id = uuid4()
            event_id = uuid4()
            expires_at = now + self._challenge_ttl
            contract = self._registry.job_contract(VERIFICATION_EMAIL_JOB_KIND)
            delivery_workflow_input = self._registry.validate_workflow_input(
                VERIFICATION_EMAIL_DELIVERY_WORKFLOW_KIND,
                {
                    "protected_workflow_id": str(command.workflow_id),
                    "challenge_id": str(challenge_id),
                },
            )
            session.add(
                WorkflowRow(
                    id=delivery_workflow_id,
                    kind=VERIFICATION_EMAIL_DELIVERY_WORKFLOW_KIND,
                    objective="Deliver one step-up verification code",
                    status="active",
                    input=delivery_workflow_input,
                    organization_party_id=workflow.organization_party_id,
                    created_at=now,
                )
            )
            await session.flush()
            session.add(
                WorkflowJobRow(
                    id=delivery_job_id,
                    workflow_id=delivery_workflow_id,
                    kind=VERIFICATION_EMAIL_JOB_KIND,
                    status="queued",
                    attempts=0,
                    max_attempts=contract.max_attempts,
                    available_at=now,
                    input={"challenge_id": str(challenge_id)},
                    created_at=now,
                )
            )
            await session.flush()
            session.add(
                WorkflowEventRow(
                    workflow_id=delivery_workflow_id,
                    job_id=delivery_job_id,
                    event_type="verification_delivery_created",
                    actor_type="system",
                    actor_id="verification_control_plane",
                    cause_type="challenge",
                    cause_id=str(challenge_id),
                    data={
                        "challenge_id": str(challenge_id),
                        "protected_workflow_id": str(command.workflow_id),
                        "destination": self._mask_email(destination.value),
                        "expires_at": expires_at.isoformat(),
                    },
                    occurred_at=now,
                )
            )
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
                    "delivery_workflow_id": str(delivery_workflow_id),
                    "delivery_job_id": str(delivery_job_id),
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
                        delivery_workflow_id=delivery_workflow_id,
                        purpose=command.purpose,
                        operation_name=command.operation.name,
                        operation_arguments=command.operation.arguments,
                        operation_fingerprint=fingerprint,
                        request_cause_id=command.cause_id,
                        destination_identifier_id=destination.id,
                        delivery_job_id=delivery_job_id,
                        created_event_id=event_id,
                        status="pending",
                        expires_at=expires_at,
                        failed_attempts=0,
                        max_attempts=5,
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

    async def begin_email_dispatch(
        self,
        *,
        run_id: UUID,
    ) -> VerificationEmailDelivery:
        """Commit the verification email dispatch boundary and reveal delivery material."""

        now = self._clock()
        rejected: str | None = None
        async with self._database.transaction() as session:
            locator = (
                await session.execute(
                    sa.select(
                        WorkflowJobRunRow.workflow_id,
                        WorkflowJobRunRow.job_id,
                    ).where(WorkflowJobRunRow.id == run_id)
                )
            ).one_or_none()
            if locator is None:
                raise StaleRunError("Verification email Run does not exist")
            delivery_workflow_id, job_id = locator
            challenge_locator = (
                await session.execute(
                    sa.select(
                        VerificationChallengeRow.id,
                        VerificationChallengeRow.workflow_id,
                    ).where(
                        VerificationChallengeRow.delivery_workflow_id == delivery_workflow_id,
                        VerificationChallengeRow.delivery_job_id == job_id,
                    )
                )
            ).one_or_none()
            if challenge_locator is None:
                raise WorkflowLifecycleError("Verification email has no Challenge")
            workflows = await self._lock_workflows(
                session,
                {delivery_workflow_id, challenge_locator.workflow_id},
            )
            delivery_workflow = workflows.get(delivery_workflow_id)
            protected_workflow = workflows.get(challenge_locator.workflow_id)
            job = await session.scalar(
                sa.select(WorkflowJobRow)
                .where(
                    WorkflowJobRow.workflow_id == delivery_workflow_id,
                    WorkflowJobRow.id == job_id,
                )
                .with_for_update()
            )
            run = await session.scalar(
                sa.select(WorkflowJobRunRow)
                .where(
                    WorkflowJobRunRow.workflow_id == delivery_workflow_id,
                    WorkflowJobRunRow.job_id == job_id,
                    WorkflowJobRunRow.id == run_id,
                )
                .with_for_update()
            )
            if (
                delivery_workflow is None
                or protected_workflow is None
                or job is None
                or run is None
                or delivery_workflow.status != "active"
                or job.kind != VERIFICATION_EMAIL_JOB_KIND
                or job.status != "running"
                or run.status != "running"
                or run.lease_expires_at <= now
            ):
                raise StaleRunError("Verification email Run has no dispatch authority")
            prior_dispatch = await session.scalar(
                sa.select(WorkflowEventRow.id).where(
                    WorkflowEventRow.workflow_id == delivery_workflow_id,
                    WorkflowEventRow.job_id == job_id,
                    WorkflowEventRow.event_type == "external_effect_dispatch_started",
                )
            )
            if prior_dispatch is not None:
                raise WorkflowLifecycleError("Verification email dispatch already started")
            challenge = await session.scalar(
                sa.select(VerificationChallengeRow)
                .where(
                    VerificationChallengeRow.id == challenge_locator.id,
                    VerificationChallengeRow.delivery_workflow_id == delivery_workflow_id,
                    VerificationChallengeRow.delivery_job_id == job_id,
                )
                .with_for_update()
            )
            if challenge is None:
                raise WorkflowLifecycleError("Verification email Challenge changed")
            destination = await session.get(
                PartyIdentifierRow,
                challenge.destination_identifier_id,
            )
            if challenge.status != "pending" or challenge.expires_at <= now:
                if challenge.status == "pending":
                    challenge.status = "expired"
                    self._add_challenge_event(
                        session,
                        challenge,
                        str(job.id),
                        "verification_expired",
                        now,
                        actor_type="system",
                        actor_id="verification_control_plane",
                        cause_type="job",
                    )
                rejected = "Verification challenge is no longer deliverable"
            elif (
                destination is None
                or destination.kind != "email"
                or destination.party_id != challenge.actor_party_id
                or destination.verified_at is None
                or destination.revoked_at is not None
                or not self._workflow_supports_purpose(
                    protected_workflow,
                    challenge.purpose,
                )
                or not await self._has_current_workflow_access(
                    session,
                    protected_workflow,
                    challenge.actor_party_id,
                )
            ):
                challenge.status = "failed"
                self._add_challenge_event(
                    session,
                    challenge,
                    str(job.id),
                    "verification_delivery_failed",
                    now,
                    actor_type="system",
                    actor_id="verification_control_plane",
                    cause_type="job",
                )
                rejected = "Verification destination is no longer valid"
            else:
                contract = self._registry.job_contract(job.kind)
                run.adapter_version = contract.adapter_version
                run.provider_tool_version = contract.provider_tool_version
                session.add(
                    WorkflowEventRow(
                        workflow_id=delivery_workflow.id,
                        job_id=job.id,
                        run_id=run.id,
                        event_type="external_effect_dispatch_started",
                        actor_type="run",
                        actor_id=str(run.id),
                        cause_type="job",
                        cause_id=str(job.id),
                        data={
                            "challenge_id": str(challenge.id),
                            "effect": "verification_email",
                        },
                        occurred_at=now,
                    )
                )
                await session.flush()
                return VerificationEmailDelivery(
                    challenge_id=challenge.id,
                    job_id=job.id,
                    run_id=run.id,
                    destination=destination.value,
                    code=self._code(challenge.id),
                    expires_at=challenge.expires_at,
                )
        raise WorkflowLifecycleError(rejected or "Verification email dispatch was rejected")

    async def validate_verified_resume(
        self,
        *,
        challenge_id: UUID,
        actor_party_id: UUID,
        interaction_id: str,
        workflow_id: UUID,
        operation: ProtectedOperation,
    ) -> VerificationDecision:
        """Revalidate one exact continuation without creating replacement work."""

        now = self._clock()
        fingerprint = self._operation_fingerprint(operation)
        async with self._database.read_transaction() as session:
            challenge = await session.scalar(
                sa.select(VerificationChallengeRow).where(
                    VerificationChallengeRow.id == challenge_id,
                    VerificationChallengeRow.actor_party_id == actor_party_id,
                    VerificationChallengeRow.interaction_id == interaction_id,
                    VerificationChallengeRow.workflow_id == workflow_id,
                    VerificationChallengeRow.operation_fingerprint == fingerprint,
                    VerificationChallengeRow.status == "verified",
                    VerificationChallengeRow.verification_session_expires_at > now,
                )
            )
            workflow = await session.get(WorkflowRow, workflow_id)
            destination = (
                await session.get(PartyIdentifierRow, challenge.destination_identifier_id)
                if challenge is not None
                else None
            )
            if (
                challenge is None
                or workflow is None
                or not self._workflow_supports_purpose(workflow, challenge.purpose)
                or destination is None
                or destination.party_id != actor_party_id
                or destination.kind != "email"
                or destination.verified_at is None
                or destination.revoked_at is not None
                or not await self._has_current_workflow_access(
                    session,
                    workflow,
                    actor_party_id,
                )
            ):
                return VerificationDecision(status="verification_unavailable")
            return VerificationDecision(
                status="session_valid",
                challenge_id=challenge.id,
                verification_session_expires_at=(challenge.verification_session_expires_at),
            )

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
        return await self._notifications.read_delivery_attention(
            notification_id=notification_id,
            workflow_event_id=workflow_event_id,
            workflow_id=workflow_id,
            worker_id=worker_id,
            delivery_attempt=delivery_attempt,
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
        return await self._notifications.read_resume_delivery(
            notification_id=notification_id,
            workflow_event_id=workflow_event_id,
            workflow_id=workflow_id,
            worker_id=worker_id,
            delivery_attempt=delivery_attempt,
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

        return await self._notifications.read_resume_recovery_destination(
            notification_id=notification_id,
            workflow_event_id=workflow_event_id,
            workflow_id=workflow_id,
            worker_id=worker_id,
            delivery_attempt=delivery_attempt,
        )

    async def submit_code(
        self,
        command: SubmitVerificationCodeCommand,
    ) -> VerificationCodeResult:
        """Atomically consume one active code and return its exact waiting operation."""

        now = self._clock()
        async with self._database.transaction() as session:
            await self._lock_interaction(session, command.actor_party_id, command.interaction_id)
            replay = await self._replayed_code_result(session, command)
            if replay is not None:
                return replay

            pending_locator = (
                await session.execute(
                    sa.select(
                        VerificationChallengeRow.id,
                        VerificationChallengeRow.workflow_id,
                        VerificationChallengeRow.delivery_workflow_id,
                    ).where(
                        VerificationChallengeRow.actor_party_id == command.actor_party_id,
                        VerificationChallengeRow.interaction_id == command.interaction_id,
                        VerificationChallengeRow.status == "pending",
                    )
                )
            ).one_or_none()
            if pending_locator is None:
                return VerificationCodeResult(status="no_active_challenge")
            workflows = await self._lock_workflows(
                session,
                {pending_locator.workflow_id, pending_locator.delivery_workflow_id},
            )
            workflow = workflows.get(pending_locator.workflow_id)
            challenge = await session.scalar(
                sa.select(VerificationChallengeRow)
                .where(
                    VerificationChallengeRow.id == pending_locator.id,
                    VerificationChallengeRow.actor_party_id == command.actor_party_id,
                    VerificationChallengeRow.interaction_id == command.interaction_id,
                    VerificationChallengeRow.status == "pending",
                )
                .with_for_update()
            )
            if challenge is None:
                return VerificationCodeResult(status="no_active_challenge")
            await self._record_verification_cause(session, command, now)
            if (
                workflow is None
                or not self._workflow_supports_purpose(workflow, challenge.purpose)
                or not await self._has_current_workflow_access(
                    session,
                    workflow,
                    command.actor_party_id,
                )
            ):
                challenge.status = "failed"
                self._add_challenge_event(
                    session,
                    challenge,
                    command.cause_id,
                    "verification_failed",
                    now,
                    submission_result="verification_unavailable",
                )
                return self._code_result(challenge, "verification_unavailable")
            if challenge.expires_at <= now:
                challenge.status = "expired"
                await self._cancel_delivery_before_dispatch(session, challenge, now)
                self._add_challenge_event(
                    session,
                    challenge,
                    command.cause_id,
                    "verification_expired",
                    now,
                    actor_type="system",
                    actor_id="verification_control_plane",
                    submission_result="expired",
                )
                return self._code_result(challenge, "expired")
            if not hmac.compare_digest(command.code, self._code(challenge.id)):
                challenge.failed_attempts += 1
                if challenge.failed_attempts >= challenge.max_attempts:
                    challenge.status = "failed"
                    self._add_challenge_event(
                        session,
                        challenge,
                        command.cause_id,
                        "verification_failed",
                        now,
                        submission_result="attempts_exhausted",
                    )
                    return self._code_result(challenge, "attempts_exhausted")
                self._add_challenge_event(
                    session,
                    challenge,
                    command.cause_id,
                    "verification_code_rejected",
                    now,
                    submission_result="invalid_code",
                    extra_data={
                        "remaining_attempts": challenge.max_attempts - challenge.failed_attempts,
                    },
                )
                return self._code_result(challenge, "invalid_code")

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
                self._add_challenge_event(
                    session,
                    challenge,
                    command.cause_id,
                    "verification_failed",
                    now,
                    submission_result="verification_unavailable",
                )
                return self._code_result(challenge, "verification_unavailable")

            if not await self._delivery_was_dispatched(session, challenge):
                challenge.status = "failed"
                self._add_challenge_event(
                    session,
                    challenge,
                    command.cause_id,
                    "verification_failed",
                    now,
                    submission_result="verification_unavailable",
                )
                return self._code_result(challenge, "verification_unavailable")

            challenge.status = "verified"
            challenge.verified_at = now
            challenge.verified_cause_id = command.cause_id
            challenge.verification_session_expires_at = now + self._session_ttl
            await self._reconcile_delivery_from_confirmed_code(session, challenge, now)
            event_id = self._add_challenge_event(
                session,
                challenge,
                command.cause_id,
                "verification_succeeded",
                now,
                submission_result="verified",
            )
            await session.flush()
            session.add(
                NotificationRow(
                    id=uuid4(),
                    workflow_id=challenge.workflow_id,
                    workflow_event_id=event_id,
                    kind=VERIFICATION_RESUME_NOTIFICATION_KIND,
                    destination_type="interaction",
                    destination_id=challenge.interaction_id,
                    status="queued",
                    attempts=0,
                    max_attempts=3,
                    available_at=now,
                    created_at=now,
                )
            )
            return self._verified_result(challenge)

    async def _replayed_code_result(
        self,
        session: AsyncSession,
        command: SubmitVerificationCodeCommand,
    ) -> VerificationCodeResult | None:
        cause = await session.get(InteractionCauseRow, command.cause_id)
        if cause is None:
            return None
        self._validate_verification_cause(cause, command)
        verified = await session.scalar(
            sa.select(VerificationChallengeRow).where(
                VerificationChallengeRow.actor_party_id == command.actor_party_id,
                VerificationChallengeRow.interaction_id == command.interaction_id,
                VerificationChallengeRow.status == "verified",
                VerificationChallengeRow.verified_cause_id == command.cause_id,
            )
        )
        if verified is not None:
            return self._verified_result(verified)
        event = await session.scalar(
            sa.select(WorkflowEventRow)
            .where(
                WorkflowEventRow.cause_id == command.cause_id,
                WorkflowEventRow.event_type.in_(
                    (
                        "verification_code_rejected",
                        "verification_failed",
                        "verification_expired",
                    )
                ),
            )
            .order_by(WorkflowEventRow.occurred_at.desc(), WorkflowEventRow.id.desc())
            .limit(1)
        )
        if event is None:
            raise WorkflowLifecycleError("Verification Cause has no committed outcome")
        try:
            challenge_id = UUID(str(event.data["challenge_id"]))
            status = cast(VerificationCodeStatus, event.data["submission_result"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowLifecycleError("Verification outcome Event is invalid") from exc
        challenge = await session.get(VerificationChallengeRow, challenge_id)
        if challenge is None:
            raise WorkflowLifecycleError("Verification outcome Challenge is missing")
        return self._code_result(challenge, status)

    async def _delivery_was_dispatched(
        self,
        session: AsyncSession,
        challenge: VerificationChallengeRow,
    ) -> bool:
        dispatch = await session.scalar(
            sa.select(WorkflowEventRow.id)
            .where(
                WorkflowEventRow.workflow_id == challenge.delivery_workflow_id,
                WorkflowEventRow.job_id == challenge.delivery_job_id,
                WorkflowEventRow.event_type == "external_effect_dispatch_started",
            )
            .limit(1)
        )
        return dispatch is not None

    async def _reconcile_delivery_from_confirmed_code(
        self,
        session: AsyncSession,
        challenge: VerificationChallengeRow,
        now: datetime,
    ) -> None:
        job = await session.scalar(
            sa.select(WorkflowJobRow)
            .where(
                WorkflowJobRow.workflow_id == challenge.delivery_workflow_id,
                WorkflowJobRow.id == challenge.delivery_job_id,
            )
            .with_for_update()
        )
        workflow = await session.get(WorkflowRow, challenge.delivery_workflow_id)
        if job is None or workflow is None:
            raise WorkflowLifecycleError("Verification delivery aggregate is missing")
        if job.status == "succeeded":
            return
        if job.status not in {"running", "waiting", "failed"}:
            return
        output = self._registry.validate_success_data(
            job.kind,
            {
                "provider": "verification_code_confirmation",
                "acknowledged": True,
                "tool_version": "verification_code_confirmation.v1",
                "message_id": None,
                "thread_id": None,
            },
        )
        active_run = None
        if job.status == "running":
            active_run = await session.scalar(
                sa.select(WorkflowJobRunRow)
                .where(
                    WorkflowJobRunRow.workflow_id == challenge.delivery_workflow_id,
                    WorkflowJobRunRow.job_id == challenge.delivery_job_id,
                    WorkflowJobRunRow.status == "running",
                )
                .with_for_update()
            )
            if active_run is None:
                raise WorkflowLifecycleError("Running verification delivery has no active Run")
            active_run.status = "succeeded"
            active_run.finished_at = now
            active_run.result = RunResult(
                outcome="succeeded",
                data=output,
                evidence=({"type": "confirmed_code_received"},),
            ).model_dump(mode="json")
        job.status = "succeeded"
        job.output = output
        session.add(
            WorkflowEventRow(
                workflow_id=workflow.id,
                job_id=job.id,
                run_id=active_run.id if active_run is not None else None,
                event_type="verification_email_reconciled",
                actor_type="system",
                actor_id="verification_control_plane",
                cause_type="challenge",
                cause_id=str(challenge.id),
                data={"evidence": "confirmed_code_received"},
                occurred_at=now,
            )
        )
        await session.flush()
        await self._completion.complete_if_satisfied(
            session,
            workflow=workflow,
            completed_job=job,
            run_id=active_run.id if active_run is not None else None,
            cause_type="challenge",
            cause_id=str(challenge.id),
            occurred_at=now,
        )

    def _add_challenge_event(
        self,
        session: AsyncSession,
        challenge: VerificationChallengeRow,
        cause_id: str,
        event_type: str,
        now: datetime,
        *,
        actor_type: str = "party",
        actor_id: str | None = None,
        cause_type: str = "message",
        submission_result: VerificationCodeStatus | None = None,
        extra_data: dict[str, object] | None = None,
    ) -> UUID:
        event_id = uuid4()
        data: dict[str, object] = {
            "challenge_id": str(challenge.id),
            "interaction_id": challenge.interaction_id,
            "purpose": challenge.purpose,
            "delivery_workflow_id": str(challenge.delivery_workflow_id),
            "delivery_job_id": str(challenge.delivery_job_id),
            "verification_session_expires_at": (
                challenge.verification_session_expires_at.isoformat()
                if challenge.verification_session_expires_at is not None
                else None
            ),
        }
        if submission_result is not None:
            data["submission_result"] = submission_result
        if extra_data is not None:
            data.update(extra_data)
        session.add(
            WorkflowEventRow(
                id=event_id,
                workflow_id=challenge.workflow_id,
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id or str(challenge.actor_party_id),
                cause_type=cause_type,
                cause_id=cause_id,
                data=data,
                occurred_at=now,
            )
        )
        return event_id

    async def _record_verification_cause(
        self,
        session: AsyncSession,
        command: SubmitVerificationCodeCommand,
        now: datetime,
    ) -> None:
        content_digest = hmac.new(
            self._code_secret,
            b"verification-cause:" + command.code.encode(),
            hashlib.sha256,
        ).hexdigest()
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
            return
        self._validate_verification_cause(interaction_cause, command, content_digest)

    def _validate_verification_cause(
        self,
        interaction_cause: InteractionCauseRow,
        command: SubmitVerificationCodeCommand,
        content_digest: str | None = None,
    ) -> None:
        expected_digest = (
            content_digest
            or hmac.new(
                self._code_secret,
                b"verification-cause:" + command.code.encode(),
                hashlib.sha256,
            ).hexdigest()
        )
        if (
            interaction_cause.actor_party_id != command.actor_party_id
            or interaction_cause.content_digest != expected_digest
        ):
            raise ValueError("Verification Cause ID conflicts with an earlier interaction")

    async def _cancel_delivery_before_dispatch(
        self,
        session: AsyncSession,
        challenge: VerificationChallengeRow,
        now: datetime,
    ) -> bool:
        job = await session.scalar(
            sa.select(WorkflowJobRow)
            .where(
                WorkflowJobRow.workflow_id == challenge.delivery_workflow_id,
                WorkflowJobRow.id == challenge.delivery_job_id,
            )
            .with_for_update()
        )
        if job is None:
            raise WorkflowLifecycleError("Verification delivery Job is missing")
        dispatch = await session.scalar(
            sa.select(WorkflowEventRow.id)
            .where(
                WorkflowEventRow.workflow_id == challenge.delivery_workflow_id,
                WorkflowEventRow.job_id == challenge.delivery_job_id,
                WorkflowEventRow.event_type == "external_effect_dispatch_started",
            )
            .limit(1)
        )
        if dispatch is not None or job.status in {"waiting", "succeeded"}:
            return False
        delivery_workflow = await session.get(WorkflowRow, challenge.delivery_workflow_id)
        if delivery_workflow is None:
            raise WorkflowLifecycleError("Verification delivery Workflow is missing")
        if job.status == "cancelled":
            return True
        run = await session.scalar(
            sa.select(WorkflowJobRunRow)
            .where(
                WorkflowJobRunRow.workflow_id == challenge.delivery_workflow_id,
                WorkflowJobRunRow.job_id == challenge.delivery_job_id,
                WorkflowJobRunRow.status == "running",
            )
            .with_for_update()
        )
        if run is not None:
            run.status = "cancelled"
            run.finished_at = now
        if job.status not in {"failed", "cancelled"}:
            job.status = "cancelled"
        delivery_workflow.status = "cancelled"
        session.add(
            WorkflowEventRow(
                workflow_id=challenge.delivery_workflow_id,
                job_id=job.id,
                run_id=run.id if run is not None else None,
                event_type="verification_delivery_cancelled",
                actor_type="system",
                actor_id="verification_control_plane",
                cause_type="challenge",
                cause_id=str(challenge.id),
                data={"challenge_id": str(challenge.id)},
                occurred_at=now,
            )
        )
        session.add(
            WorkflowEventRow(
                workflow_id=challenge.delivery_workflow_id,
                job_id=job.id,
                event_type="workflow_cancelled",
                actor_type="system",
                actor_id="verification_control_plane",
                cause_type="challenge",
                cause_id=str(challenge.id),
                data={"reason": "verification_challenge_ended"},
                occurred_at=now,
            )
        )
        return True

    async def _challenge_limit_reached(
        self,
        session: AsyncSession,
        actor_party_id: UUID,
        destination_identifier_id: UUID,
        now: datetime,
    ) -> bool:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(VerificationChallengeRow)
            .where(
                VerificationChallengeRow.actor_party_id == actor_party_id,
                VerificationChallengeRow.destination_identifier_id == destination_identifier_id,
                VerificationChallengeRow.created_at >= now - timedelta(hours=1),
            )
        )
        return int(count or 0) >= self._max_challenges_per_hour

    @staticmethod
    async def _has_current_workflow_access(
        session: AsyncSession,
        workflow: WorkflowRow,
        actor_party_id: UUID,
    ) -> bool:
        return await has_current_workflow_access(session, workflow.id, actor_party_id)

    @staticmethod
    def _workflow_supports_purpose(workflow: WorkflowRow, purpose: str) -> bool:
        return workflow.status == "active" or (
            purpose == "sensitive_read" and workflow.status in {"completed", "cancelled"}
        )

    @staticmethod
    async def _lock_workflows(
        session: AsyncSession,
        workflow_ids: set[UUID],
    ) -> dict[UUID, WorkflowRow]:
        rows = (
            await session.scalars(
                sa.select(WorkflowRow)
                .where(WorkflowRow.id.in_(workflow_ids))
                .order_by(WorkflowRow.id)
                .with_for_update()
            )
        ).all()
        return {row.id: row for row in rows}

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
    async def _lock_verification_destination(
        session: AsyncSession,
        actor_party_id: UUID,
        destination_identifier_id: UUID,
    ) -> None:
        material = f"verification-destination:{actor_party_id}:{destination_identifier_id}".encode()
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
    def _code_result(
        challenge: VerificationChallengeRow,
        status: VerificationCodeStatus,
    ) -> VerificationCodeResult:
        return VerificationCodeResult(
            status=status,
            challenge_id=challenge.id,
            workflow_id=challenge.workflow_id,
            purpose=cast(
                Literal["sensitive_read", "sensitive_write"],
                challenge.purpose,
            ),
        )

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
            verification_session_expires_at=challenge.verification_session_expires_at,
        )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(UTC)


__all__ = ["VERIFICATION_RESUME_NOTIFICATION_KIND", "StepUpVerification"]
