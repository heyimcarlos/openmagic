from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from server.agents.interaction_agent.verification_resume import (
    VerificationResumeInteractionFactory,
    VerificationResumeRecoveryInteraction,
    VerificationResumeRecoveryInteractionFactory,
)
from server.config import Settings
from server.tests.workflows.retrieval_fixtures import (
    ACME_ID,
    BROKER_ID,
    JOHN_ACME_ID,
    TARGET_ID,
    WRONG_KIND_ID,
    seed_retrieval_landscape,
)
from server.workflows import (
    VERIFICATION_DELIVERY_ATTENTION_NOTIFICATION_KIND,
    VERIFICATION_RESUME_NOTIFICATION_KIND,
    VERIFICATION_RESUME_RECOVERY_NOTIFICATION_KIND,
    AuthorizeProtectedOperationCommand,
    CancelWorkflowCommand,
    ClaimNotificationCommand,
    ClaimWorkflowJobCommand,
    DeterministicVerificationEmailSender,
    NotificationWorker,
    ProtectedOperation,
    ReportRunResultCommand,
    RunResult,
    RunResultConflictError,
    StaticWorkflowAuthority,
    StepUpVerification,
    SubmitVerificationCodeCommand,
    VerificationEmailExecutionHandler,
    WorkflowCommandContext,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowWorker,
    default_workflow_registry,
)


def _request(cause_id: str = "private-read-message") -> AuthorizeProtectedOperationCommand:
    return AuthorizeProtectedOperationCommand(
        actor_party_id=JOHN_ACME_ID,
        interaction_id="sms-policyholder-demo",
        workflow_id=TARGET_ID,
        purpose="sensitive_read",
        cause_id=cause_id,
        operation=ProtectedOperation(
            name="read_workflow_packet",
            arguments={"workflow_id": str(TARGET_ID)},
        ),
    )


def _control_plane(database: WorkflowDatabase) -> WorkflowControlPlane:
    return WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )


async def _deliver_code(
    database: WorkflowDatabase,
    verification: StepUpVerification,
    *,
    sender: DeterministicVerificationEmailSender | None = None,
):
    resolved_sender = sender or DeterministicVerificationEmailSender()
    worker = WorkflowWorker(
        control_plane=_control_plane(database),
        executors={},
        deterministic_handlers={
            "composio_verification_email": VerificationEmailExecutionHandler(
                verification=verification,
                sender=resolved_sender,
            )
        },
        worker_id="verification-email-worker",
        application_build="verification-test",
    )
    packet = await worker.run_once()
    assert packet is not None
    assert len(resolved_sender.deliveries) == 1
    return resolved_sender.deliveries[0]


async def test_private_operation_survives_restart_and_queues_exact_resume(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    now = datetime.now(UTC)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-test-secret",
        clock=lambda: now,
    )

    required = await verification.authorize_or_challenge(_request())

    assert required.status == "verification_required"
    assert required.masked_destination == "j***@example.com"
    assert required.expires_at == now + timedelta(minutes=10)
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        delivery_creation_events = await connection.scalar(
            sa.text(
                "SELECT count(*) FROM workflow_events "
                "WHERE workflow_id = (SELECT delivery_workflow_id "
                "FROM verification_challenges WHERE id = :challenge_id) "
                "AND event_type = 'verification_delivery_created'"
            ),
            {"challenge_id": required.challenge_id},
        )
    await engine.dispose()
    assert delivery_creation_events == 1
    delivery = await _deliver_code(database, verification)
    assert delivery.destination == "john@example.com"
    assert delivery.code.isdigit()

    await database.dispose()
    restarted_database = WorkflowDatabase(migrated_postgres_url)
    restarted = StepUpVerification(
        database=restarted_database,
        code_secret=b"verification-test-secret",
        clock=lambda: now,
    )
    verified = await restarted.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="verification-code-message",
            code=delivery.code,
        )
    )

    assert verified.status == "verified"
    assert verified.workflow_id == TARGET_ID
    assert verified.operation == _request().operation
    assert verified.verification_session_expires_at == now + timedelta(minutes=15)

    notification = await _control_plane(restarted_database).claim_notification(
        ClaimNotificationCommand(
            worker_id="verification-resume-worker",
            lease_duration=timedelta(minutes=5),
            kinds=(VERIFICATION_RESUME_NOTIFICATION_KIND,),
        )
    )
    assert notification is not None
    resume = await restarted.read_resume_delivery(
        notification_id=notification.notification_id,
        workflow_event_id=notification.workflow_event_id,
        workflow_id=notification.workflow_id,
        worker_id="verification-resume-worker",
        delivery_attempt=notification.delivery_attempt,
    )
    assert resume.operation == verified.operation
    assert resume.request_cause_id == "private-read-message"
    await restarted_database.dispose()


async def test_fresh_worker_resumes_committed_verification_after_request_process_loss(
    migrated_postgres_url: str,
    clean_workflow_database,
    monkeypatch,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-resume-worker-secret",
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)
    await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="verification-code-message",
            code=delivery.code,
        )
    )

    resumed: list[dict[str, object]] = []

    class FakeRuntime:
        async def execute_verified_resume(self, **kwargs):
            resumed.append(kwargs)
            return SimpleNamespace(success=True, response="Your renewal is active.")

    monkeypatch.setattr(
        "server.agents.interaction_agent.factory.create_interaction_runtime",
        lambda *_args, **_kwargs: FakeRuntime(),
    )
    worker = NotificationWorker(
        control_plane=_control_plane(database),
        interactions=VerificationResumeInteractionFactory(
            verification=StepUpVerification(
                database=database,
                code_secret=b"verification-resume-worker-secret",
            ),
            settings=Settings(),
        ),
        worker_id="fresh-verification-resume-worker",
        notification_kinds=(VERIFICATION_RESUME_NOTIFICATION_KIND,),
    )

    delivered = await worker.run_once()

    assert delivered is not None
    assert delivered.kind == VERIFICATION_RESUME_NOTIFICATION_KIND
    assert len(resumed) == 1
    assert resumed[0]["operation_cause_id"] == "private-read-message"
    assert resumed[0]["operation"] == _request().operation
    await database.dispose()


async def test_transient_resume_failure_requeues_notification(
    migrated_postgres_url: str,
    clean_workflow_database,
    monkeypatch,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-resume-retry-secret",
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)
    await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="verification-resume-retry-code",
            code=delivery.code,
        )
    )

    class FailingRuntime:
        async def execute_verified_resume(self, **_kwargs):
            return SimpleNamespace(success=False, response="")

    monkeypatch.setattr(
        "server.agents.interaction_agent.factory.create_interaction_runtime",
        lambda *_args, **_kwargs: FailingRuntime(),
    )
    worker = NotificationWorker(
        control_plane=_control_plane(database),
        interactions=VerificationResumeInteractionFactory(
            verification=verification,
            settings=Settings(),
        ),
        worker_id="failing-verification-resume-worker",
        notification_kinds=(VERIFICATION_RESUME_NOTIFICATION_KIND,),
    )

    with pytest.raises(RuntimeError, match="no user-facing response"):
        await worker.run_once()

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                sa.text(
                    "SELECT status, attempts, last_error FROM notifications WHERE kind = :kind"
                ),
                {"kind": VERIFICATION_RESUME_NOTIFICATION_KIND},
            )
        ).one()
    await engine.dispose()
    assert tuple(row) == ("queued", 1, "interaction_delivery_failed")
    await database.dispose()


async def test_terminal_resume_failure_records_deterministic_recovery(
    migrated_postgres_url: str,
    clean_workflow_database,
    monkeypatch,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-resume-terminal-secret",
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)
    await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="verification-resume-terminal-code",
            code=delivery.code,
        )
    )
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text("UPDATE notifications SET max_attempts = 1 WHERE kind = :kind"),
            {"kind": VERIFICATION_RESUME_NOTIFICATION_KIND},
        )
    await engine.dispose()

    class FailingInteraction:
        async def handle(self, *_args):
            raise RuntimeError("presentation unavailable")

    class FailingFactory:
        @asynccontextmanager
        async def create(self, *_args):
            yield FailingInteraction()

    class ReplyLog:
        def __init__(self) -> None:
            self.messages: dict[str, str] = {}

        def record_reply_once(
            self,
            delivery_id: str,
            message: str,
            *,
            cause_id: str | None = None,
        ) -> bool:
            if delivery_id in self.messages:
                return False
            self.messages[delivery_id] = message
            return True

    reply_log = ReplyLog()
    monkeypatch.setattr(
        "server.agents.interaction_agent.verification_resume.get_conversation_session",
        lambda _interaction_id: SimpleNamespace(log=reply_log),
    )
    worker = NotificationWorker(
        control_plane=_control_plane(database),
        interactions=FailingFactory(),
        worker_id="terminal-verification-resume-worker",
        notification_kinds=(VERIFICATION_RESUME_NOTIFICATION_KIND,),
    )

    with pytest.raises(RuntimeError, match="presentation unavailable"):
        await worker.run_once()

    assert reply_log.messages == {}
    recovery_worker = NotificationWorker(
        control_plane=_control_plane(database),
        interactions=VerificationResumeRecoveryInteractionFactory(
            verification=verification,
        ),
        worker_id="verification-resume-recovery-worker",
        notification_kinds=(VERIFICATION_RESUME_RECOVERY_NOTIFICATION_KIND,),
    )
    recovered = await recovery_worker.run_once()

    assert recovered is not None
    assert recovered.status == "delivered"
    assert tuple(reply_log.messages.values()) == (VerificationResumeRecoveryInteraction.MESSAGE,)
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                sa.text(
                    "SELECT status, attempts, max_attempts, last_error "
                    "FROM notifications WHERE kind = :kind"
                ),
                {"kind": VERIFICATION_RESUME_NOTIFICATION_KIND},
            )
        ).one()
    await engine.dispose()
    assert tuple(row) == ("failed", 1, 1, "interaction_delivery_failed")
    await database.dispose()


async def test_worker_loss_on_final_resume_attempt_queues_durable_recovery(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = _control_plane(database)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-resume-crash-secret",
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)
    await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="verification-resume-crash-code",
            code=delivery.code,
        )
    )
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text("UPDATE notifications SET max_attempts = 1 WHERE kind = :kind"),
            {"kind": VERIFICATION_RESUME_NOTIFICATION_KIND},
        )
    await engine.dispose()
    lost = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="lost-verification-resume-worker",
            lease_duration=timedelta(milliseconds=100),
            kinds=(VERIFICATION_RESUME_NOTIFICATION_KIND,),
        )
    )
    assert lost is not None
    await asyncio.sleep(0.15)

    recovery = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="verification-resume-recovery-worker",
            lease_duration=timedelta(minutes=5),
            kinds=(VERIFICATION_RESUME_RECOVERY_NOTIFICATION_KIND,),
        )
    )

    assert recovery is not None
    assert recovery.kind == VERIFICATION_RESUME_RECOVERY_NOTIFICATION_KIND
    assert (
        await verification.read_resume_recovery_destination(
            notification_id=recovery.notification_id,
            workflow_event_id=recovery.workflow_event_id,
            workflow_id=recovery.workflow_id,
            worker_id="verification-resume-recovery-worker",
            delivery_attempt=recovery.delivery_attempt,
        )
        == "sms-policyholder-demo"
    )
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        source = (
            await connection.execute(
                sa.text(
                    "SELECT status, attempts, max_attempts, last_error "
                    "FROM notifications WHERE id = :notification_id"
                ),
                {"notification_id": lost.notification_id},
            )
        ).one()
        recovery_events = await connection.scalar(
            sa.text(
                "SELECT count(*) FROM workflow_events "
                "WHERE event_type = 'verification_resume_delivery_failed' "
                "AND cause_id = :notification_id"
            ),
            {"notification_id": str(lost.notification_id)},
        )
    await engine.dispose()
    assert tuple(source) == ("failed", 1, 1, "delivery_lease_expired")
    assert recovery_events == 1
    await database.dispose()


async def test_fifteen_minute_session_covers_later_protected_purposes(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    now = datetime.now(UTC)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-session-secret",
        clock=lambda: now,
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)
    await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="verification-code-message",
            code=delivery.code,
        )
    )

    write = _request("later-sensitive-write").model_copy(
        update={
            "purpose": "sensitive_write",
            "operation": ProtectedOperation(
                name="approve_job",
                arguments={
                    "job_id": "60000000-0000-0000-0000-000000000001",
                    "expected_draft_revision_id": "60000000-0000-0000-0000-000000000002",
                },
            ),
        }
    )
    authorized = await verification.authorize_or_challenge(write)

    assert authorized.status == "session_valid"
    assert authorized.verification_session_expires_at == now + timedelta(minutes=15)
    await database.dispose()


async def test_session_crosses_authorized_workflows_and_expires_at_the_boundary(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    current = [datetime.now(UTC)]
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-session-boundary-secret",
        clock=lambda: current[0],
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)
    verified = await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="session-boundary-code",
            code=delivery.code,
        )
    )
    cross_workflow = _request("cross-workflow-read").model_copy(
        update={
            "workflow_id": WRONG_KIND_ID,
            "operation": ProtectedOperation(
                name="read_workflow_packet",
                arguments={"workflow_id": str(WRONG_KIND_ID)},
            ),
        }
    )

    assert (await verification.authorize_or_challenge(cross_workflow)).status == "session_valid"

    current[0] = verified.verification_session_expires_at
    expired = await verification.validate_verified_resume(
        challenge_id=verified.challenge_id,
        actor_party_id=JOHN_ACME_ID,
        interaction_id="sms-policyholder-demo",
        workflow_id=TARGET_ID,
        operation=_request().operation,
    )
    assert expired.status == "verification_unavailable"

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        challenge_count = await connection.scalar(
            sa.text("SELECT count(*) FROM verification_challenges")
        )
    await engine.dispose()
    assert challenge_count == 1
    await database.dispose()


async def test_session_is_not_shared_with_another_interaction_or_party(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-session-scope-secret",
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)
    await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="session-scope-code",
            code=delivery.code,
        )
    )

    another_interaction = _request("another-interaction").model_copy(
        update={"interaction_id": "sms-policyholder-other"}
    )
    another_party = _request("another-party").model_copy(
        update={"actor_party_id": BROKER_ID, "interaction_id": "sms-broker-demo"}
    )

    assert (
        await verification.authorize_or_challenge(another_interaction)
    ).status == "verification_required"
    assert (
        await verification.authorize_or_challenge(another_party)
    ).status == "verification_required"
    await database.dispose()


async def test_current_authority_is_revalidated_during_the_session(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-session-authority-secret",
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)
    await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="session-authority-code",
            code=delivery.code,
        )
    )

    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE workflow_participant_roles SET revoked_at = now() "
                "WHERE workflow_id = :workflow_id AND party_id = :party_id "
                "AND role = 'Policyholder'"
            ),
            {"workflow_id": TARGET_ID, "party_id": JOHN_ACME_ID},
        )
    await engine.dispose()

    assert (
        await verification.authorize_or_challenge(_request("after-authority-revocation"))
    ).status == "verification_unavailable"
    await database.dispose()


async def test_verified_identifier_revocation_ends_the_session(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-session-identifier-secret",
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)
    verified = await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="session-identifier-code",
            code=delivery.code,
        )
    )

    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE party_identifiers SET revoked_at = now() "
                "WHERE party_id = :party_id AND kind = 'email'"
            ),
            {"party_id": JOHN_ACME_ID},
        )
    await engine.dispose()

    decision = await verification.validate_verified_resume(
        challenge_id=verified.challenge_id,
        actor_party_id=JOHN_ACME_ID,
        interaction_id="sms-policyholder-demo",
        workflow_id=TARGET_ID,
        operation=_request().operation,
    )
    assert decision.status == "verification_unavailable"
    await database.dispose()


async def test_verification_delivery_isolated_from_business_cancellation(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = _control_plane(database)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-cancellation-boundary-secret",
    )
    required = await verification.authorize_or_challenge(_request())
    await _deliver_code(database, verification)

    result = await control_plane.cancel_workflow(
        CancelWorkflowCommand(
            context=WorkflowCommandContext(
                actor_party_id=BROKER_ID,
                organization_party_id=ACME_ID,
                cause_type="message",
                cause_id="cancel-after-verification-email",
            ),
            workflow_id=TARGET_ID,
        )
    )

    assert result.outcome == "cancelled"
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                sa.text(
                    "SELECT c.delivery_workflow_id, d.status, b.status "
                    "FROM verification_challenges c "
                    "JOIN workflows d ON d.id = c.delivery_workflow_id "
                    "JOIN workflows b ON b.id = c.workflow_id "
                    "WHERE c.id = :challenge_id"
                ),
                {"challenge_id": required.challenge_id},
            )
        ).one()
        business_verification_jobs = await connection.scalar(
            sa.text(
                "SELECT count(*) FROM workflow_jobs "
                "WHERE workflow_id = :workflow_id AND kind = 'verification.email_code.v1'"
            ),
            {"workflow_id": TARGET_ID},
        )
    await engine.dispose()
    assert row.delivery_workflow_id != TARGET_ID
    assert row[1] == "completed"
    assert row[2] == "cancelled"
    assert business_verification_jobs == 0
    await database.dispose()


async def test_uncertain_email_outcome_waits_without_automatic_retry(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-uncertain-secret",
    )
    required = await verification.authorize_or_challenge(_request())
    sender = DeterministicVerificationEmailSender(
        invocation_error=TimeoutError("provider response lost")
    )
    worker = WorkflowWorker(
        control_plane=_control_plane(database),
        executors={},
        deterministic_handlers={
            "composio_verification_email": VerificationEmailExecutionHandler(
                verification=verification,
                sender=sender,
            )
        },
        worker_id="verification-email-worker",
        application_build="verification-test",
    )

    await worker.run_once()
    assert await worker.run_once() is None
    assert len(sender.deliveries) == 1

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                sa.text(
                    "SELECT j.status, r.status, r.result->>'outcome' AS outcome "
                    "FROM workflow_jobs j JOIN workflow_job_runs r ON r.job_id = j.id "
                    "WHERE j.id = (SELECT delivery_job_id FROM verification_challenges "
                    "WHERE id = :challenge_id)"
                ),
                {"challenge_id": required.challenge_id},
            )
        ).one()
        target_run_count = await connection.scalar(
            sa.text("SELECT count(*) FROM workflow_job_runs WHERE workflow_id = :workflow_id"),
            {"workflow_id": TARGET_ID},
        )
    await engine.dispose()
    assert tuple(row) == ("waiting", "failed", "uncertain")
    assert target_run_count == 0

    notification = await _control_plane(database).claim_notification(
        ClaimNotificationCommand(
            worker_id="verification-attention-worker",
            lease_duration=timedelta(minutes=5),
            kinds=(VERIFICATION_DELIVERY_ATTENTION_NOTIFICATION_KIND,),
        )
    )
    assert notification is not None
    attention = await verification.read_delivery_attention(
        notification_id=notification.notification_id,
        workflow_event_id=notification.workflow_event_id,
        workflow_id=notification.workflow_id,
        worker_id="verification-attention-worker",
        delivery_attempt=notification.delivery_attempt,
    )
    assert "will not send another automatically" in attention.message
    assert "If your verification already succeeded" in attention.message
    await database.dispose()


async def test_known_terminal_failure_before_dispatch_fails_without_a_provider_call(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    current = [datetime.now(UTC)]
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = _control_plane(database)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-terminal-failure-secret",
        clock=lambda: current[0],
    )
    required = await verification.authorize_or_challenge(_request())
    packet = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="verification-email-worker",
            application_build="verification-test",
            lease_duration=timedelta(minutes=30),
            executor_keys=("composio_verification_email",),
        )
    )
    assert packet is not None
    current[0] += timedelta(minutes=11)
    sender = DeterministicVerificationEmailSender()
    result = await VerificationEmailExecutionHandler(
        verification=verification,
        sender=sender,
    ).execute(packet)

    assert result.outcome == "failed"
    assert sender.deliveries == ()
    committed = await control_plane.report_run_result(
        ReportRunResultCommand(run_id=packet.run_id, result=result)
    )
    assert committed.job_status == "failed"
    assert committed.run_status == "failed"
    assert (
        await control_plane.claim_job(
            ClaimWorkflowJobCommand(
                worker_id="verification-email-worker",
                application_build="verification-test",
                lease_duration=timedelta(minutes=5),
                executor_keys=("composio_verification_email",),
            )
        )
        is None
    )

    notification = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="verification-attention-worker",
            lease_duration=timedelta(minutes=30),
            kinds=(VERIFICATION_DELIVERY_ATTENTION_NOTIFICATION_KIND,),
        )
    )
    assert notification is not None
    attention = await verification.read_delivery_attention(
        notification_id=notification.notification_id,
        workflow_event_id=notification.workflow_event_id,
        workflow_id=notification.workflow_id,
        worker_id="verification-attention-worker",
        delivery_attempt=notification.delivery_attempt,
    )
    assert attention.message == (
        "I could not send the verification email. Please try your request again."
    )
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        state = (
            await connection.execute(
                sa.text(
                    "SELECT c.status, j.attempts, j.max_attempts "
                    "FROM verification_challenges c "
                    "JOIN workflow_jobs j ON j.id = c.delivery_job_id "
                    "WHERE c.id = :challenge_id"
                ),
                {"challenge_id": required.challenge_id},
            )
        ).one()
    await engine.dispose()
    assert tuple(state) == ("expired", 1, 3)
    await database.dispose()


async def test_worker_loss_after_dispatch_waits_without_automatic_retry(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    control_plane = _control_plane(database)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-post-dispatch-loss-secret",
    )
    required = await verification.authorize_or_challenge(_request())
    packet = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="lost-verification-email-worker",
            application_build="verification-test",
            lease_duration=timedelta(milliseconds=100),
            executor_keys=("composio_verification_email",),
        )
    )
    assert packet is not None
    await verification.begin_email_dispatch(run_id=packet.run_id)
    await asyncio.sleep(0.15)

    replacement = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="replacement-verification-email-worker",
            application_build="verification-test",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_verification_email",),
        )
    )

    assert replacement is None
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        state = (
            await connection.execute(
                sa.text(
                    "SELECT j.status, j.attempts, r.status, r.result "
                    "FROM verification_challenges c "
                    "JOIN workflow_jobs j ON j.id = c.delivery_job_id "
                    "JOIN workflow_job_runs r ON r.id = :run_id "
                    "WHERE c.id = :challenge_id"
                ),
                {"challenge_id": required.challenge_id, "run_id": packet.run_id},
            )
        ).one()
    await engine.dispose()
    assert tuple(state) == ("waiting", 1, "abandoned", None)

    notification = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="verification-attention-worker",
            lease_duration=timedelta(minutes=5),
            kinds=(VERIFICATION_DELIVERY_ATTENTION_NOTIFICATION_KIND,),
        )
    )
    assert notification is not None
    attention = await verification.read_delivery_attention(
        notification_id=notification.notification_id,
        workflow_event_id=notification.workflow_event_id,
        workflow_id=notification.workflow_id,
        worker_id="verification-attention-worker",
        delivery_attempt=notification.delivery_attempt,
    )
    assert attention.message is not None
    assert "will not send another automatically" in attention.message
    await database.dispose()


async def test_confirmed_code_reconciles_a_still_running_delivery(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-running-reconciliation-secret",
    )
    required = await verification.authorize_or_challenge(_request())
    control_plane = _control_plane(database)
    packet = await control_plane.claim_job(
        ClaimWorkflowJobCommand(
            worker_id="verification-email-worker",
            application_build="verification-test",
            lease_duration=timedelta(minutes=5),
            executor_keys=("composio_verification_email",),
        )
    )
    assert packet is not None
    delivery = await verification.begin_email_dispatch(run_id=packet.run_id)

    verified = await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="verification-code-message",
            code=delivery.code,
        )
    )

    assert verified.status == "verified"
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        state = (
            await connection.execute(
                sa.text(
                    "SELECT w.status, j.status, r.status, r.result->>'outcome' "
                    "FROM verification_challenges c "
                    "JOIN workflows w ON w.id = c.delivery_workflow_id "
                    "JOIN workflow_jobs j ON j.id = c.delivery_job_id "
                    "JOIN workflow_job_runs r ON r.id = :run_id "
                    "WHERE c.id = :challenge_id"
                ),
                {"challenge_id": required.challenge_id, "run_id": packet.run_id},
            )
        ).one()
        attention_count = await connection.scalar(
            sa.text(
                "SELECT count(*) FROM notifications "
                "WHERE kind = :kind AND workflow_id = :workflow_id"
            ),
            {
                "kind": VERIFICATION_DELIVERY_ATTENTION_NOTIFICATION_KIND,
                "workflow_id": packet.workflow_id,
            },
        )
    await engine.dispose()
    assert tuple(state) == ("completed", "succeeded", "succeeded", "succeeded")
    assert attention_count == 0
    with pytest.raises(RunResultConflictError):
        await control_plane.report_run_result(
            ReportRunResultCommand(
                run_id=packet.run_id,
                result=RunResult(
                    outcome="uncertain",
                    evidence=({"type": "late_provider_result"},),
                    error={"code": "provider_response_lost"},
                ),
            )
        )
    await database.dispose()


async def test_code_confirmation_suppresses_a_queued_uncertainty_warning(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-stale-attention-secret",
    )
    await verification.authorize_or_challenge(_request())
    sender = DeterministicVerificationEmailSender(
        invocation_error=TimeoutError("provider response lost")
    )
    await _deliver_code(database, verification, sender=sender)
    delivery = sender.deliveries[0]
    assert (
        await verification.submit_code(
            SubmitVerificationCodeCommand(
                actor_party_id=JOHN_ACME_ID,
                interaction_id="sms-policyholder-demo",
                cause_id="verification-code-message",
                code=delivery.code,
            )
        )
    ).status == "verified"

    notification = await _control_plane(database).claim_notification(
        ClaimNotificationCommand(
            worker_id="verification-attention-worker",
            lease_duration=timedelta(minutes=5),
            kinds=(VERIFICATION_DELIVERY_ATTENTION_NOTIFICATION_KIND,),
        )
    )
    assert notification is not None
    attention = await verification.read_delivery_attention(
        notification_id=notification.notification_id,
        workflow_event_id=notification.workflow_event_id,
        workflow_id=notification.workflow_id,
        worker_id="verification-attention-worker",
        delivery_attempt=notification.delivery_attempt,
    )
    assert attention.message is None

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        state = (
            await connection.execute(
                sa.text(
                    "SELECT w.status, j.status FROM verification_challenges c "
                    "JOIN workflows w ON w.id = c.delivery_workflow_id "
                    "JOIN workflow_jobs j ON j.id = c.delivery_job_id "
                    "WHERE c.id = :challenge_id"
                ),
                {"challenge_id": delivery.challenge_id},
            )
        ).one()
    await engine.dispose()
    assert tuple(state) == ("completed", "succeeded")
    await database.dispose()


async def test_authorized_sensitive_read_can_verify_a_completed_workflow(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    engine = create_async_engine(migrated_postgres_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text("UPDATE workflows SET status = 'completed' WHERE id = :workflow_id"),
            {"workflow_id": TARGET_ID},
        )
    await engine.dispose()
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-historical-read-secret",
    )

    required = await verification.authorize_or_challenge(_request())
    assert required.status == "verification_required"
    delivery = await _deliver_code(database, verification)
    verified = await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="verification-code-message",
            code=delivery.code,
        )
    )

    assert verified.status == "verified"
    write = _request("historical-write").model_copy(
        update={
            "purpose": "sensitive_write",
            "operation": ProtectedOperation(
                name="propose_renewal_email",
                arguments={"workflow_id": str(TARGET_ID)},
            ),
        }
    )
    assert (await verification.authorize_or_challenge(write)).status == "verification_unavailable"
    await database.dispose()


async def test_unknown_or_malformed_protected_operation_creates_no_domain_state(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)

    with pytest.raises(ValidationError):
        ProtectedOperation(name="delete_everything", arguments={})
    with pytest.raises(ValidationError):
        ProtectedOperation(name="read_workflow_packet", arguments={"workflow_id": "not-a-uuid"})

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        challenge_count = await connection.scalar(
            sa.text("SELECT count(*) FROM verification_challenges")
        )
    await engine.dispose()
    assert challenge_count == 0


async def test_dispatched_challenge_cannot_be_silently_replaced(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-dispatch-wins-secret",
    )
    first = await verification.authorize_or_challenge(_request("first-protected-request"))
    await _deliver_code(database, verification)
    changed = _request("changed-protected-request").model_copy(
        update={
            "operation": ProtectedOperation(
                name="propose_renewal_email",
                arguments={"workflow_id": str(TARGET_ID)},
            )
        }
    )

    decision = await verification.authorize_or_challenge(changed)

    assert decision.status == "verification_in_progress"
    assert decision.challenge_id == first.challenge_id
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        challenge_count = await connection.scalar(
            sa.text("SELECT count(*) FROM verification_challenges")
        )
        delivery_count = await connection.scalar(
            sa.text("SELECT count(*) FROM workflow_jobs WHERE kind = 'verification.email_code.v1'")
        )
    await engine.dispose()
    assert challenge_count == 1
    assert delivery_count == 1
    await database.dispose()


async def test_concurrent_identical_submission_is_replay_safe(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-concurrency-secret",
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)
    submission = SubmitVerificationCodeCommand(
        actor_party_id=JOHN_ACME_ID,
        interaction_id="sms-policyholder-demo",
        cause_id="verification-code-message",
        code=delivery.code,
    )

    first, second = await asyncio.gather(
        verification.submit_code(submission),
        verification.submit_code(submission),
    )

    assert first == second
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        resume_count = await connection.scalar(
            sa.text("SELECT count(*) FROM notifications WHERE kind = :kind"),
            {"kind": VERIFICATION_RESUME_NOTIFICATION_KIND},
        )
    await engine.dispose()
    assert resume_count == 1
    await database.dispose()


async def test_challenge_issuance_is_rate_limited_across_supersession(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-rate-limit-secret",
        max_challenges_per_hour=2,
    )
    first = await verification.authorize_or_challenge(_request("request-1"))
    second = await verification.authorize_or_challenge(
        _request("request-2").model_copy(
            update={
                "operation": ProtectedOperation(
                    name="propose_renewal_email",
                    arguments={"workflow_id": str(TARGET_ID)},
                )
            }
        )
    )
    third = await verification.authorize_or_challenge(
        _request("request-3").model_copy(
            update={
                "operation": ProtectedOperation(
                    name="approve_job",
                    arguments={
                        "job_id": str(uuid4()),
                        "expected_draft_revision_id": str(uuid4()),
                    },
                )
            }
        )
    )

    assert first.challenge_id != second.challenge_id
    assert third.challenge_id == second.challenge_id
    await database.dispose()


async def test_delivery_unavailable_creates_no_challenge_or_job(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-unavailable-secret",
        delivery_available=False,
    )

    decision = await verification.authorize_or_challenge(_request())

    assert decision.status == "verification_unavailable"
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        challenge_count = await connection.scalar(
            sa.text("SELECT count(*) FROM verification_challenges")
        )
        job_count = await connection.scalar(
            sa.text("SELECT count(*) FROM workflow_jobs WHERE kind = 'verification.email_code.v1'")
        )
    await engine.dispose()
    assert challenge_count == 0
    assert job_count == 0
    await database.dispose()


async def test_wrong_code_attempts_exhaust_and_code_is_single_use(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-attempt-secret",
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)

    for attempt in range(1, 6):
        result = await verification.submit_code(
            SubmitVerificationCodeCommand(
                actor_party_id=JOHN_ACME_ID,
                interaction_id="sms-policyholder-demo",
                cause_id=f"wrong-code-{attempt}",
                code="000000" if delivery.code != "000000" else "999999",
            )
        )
    assert result.status == "attempts_exhausted"
    assert (
        await verification.submit_code(
            SubmitVerificationCodeCommand(
                actor_party_id=JOHN_ACME_ID,
                interaction_id="sms-policyholder-demo",
                cause_id="late-correct-code",
                code=delivery.code,
            )
        )
    ).status == "no_active_challenge"
    await database.dispose()


async def test_duplicate_wrong_code_cause_counts_once(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-wrong-code-replay-secret",
    )
    await verification.authorize_or_challenge(_request())
    delivery = await _deliver_code(database, verification)
    submission = SubmitVerificationCodeCommand(
        actor_party_id=JOHN_ACME_ID,
        interaction_id="sms-policyholder-demo",
        cause_id="duplicate-wrong-code",
        code="000000" if delivery.code != "000000" else "999999",
    )

    first = await verification.submit_code(submission)
    second = await verification.submit_code(submission)

    assert first == second
    assert first.status == "invalid_code"
    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        failed_attempts = await connection.scalar(
            sa.text("SELECT failed_attempts FROM verification_challenges WHERE id = :challenge_id"),
            {"challenge_id": first.challenge_id},
        )
        rejection_events = await connection.scalar(
            sa.text(
                "SELECT count(*) FROM workflow_events "
                "WHERE event_type = 'verification_code_rejected' "
                "AND cause_id = 'duplicate-wrong-code'"
            )
        )
    await engine.dispose()
    assert failed_attempts == 1
    assert rejection_events == 1
    await database.dispose()
