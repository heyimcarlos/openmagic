from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.tests.workflows.retrieval_fixtures import (
    JOHN_ACME_ID,
    TARGET_ID,
    seed_retrieval_landscape,
)
from server.workflows import (
    AuthorizeProtectedOperationCommand,
    ClaimNotificationCommand,
    DeterministicVerificationEmailSender,
    NotificationWorker,
    ProtectedOperation,
    StaticWorkflowAuthority,
    StepUpVerification,
    SubmitVerificationCodeCommand,
    VerificationDeliveryFailureHandler,
    VerificationEmailInteractionFactory,
    WorkflowControlPlane,
    WorkflowDatabase,
    default_workflow_registry,
)


async def test_private_operation_resumes_after_email_code_and_process_restart(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    now = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-test-secret",
        clock=lambda: now,
    )
    operation = ProtectedOperation(
        name="read_workflow_packet",
        arguments={"workflow_id": str(TARGET_ID)},
    )
    request = AuthorizeProtectedOperationCommand(
        actor_party_id=JOHN_ACME_ID,
        interaction_id="sms-policyholder-demo",
        workflow_id=TARGET_ID,
        purpose="sensitive_read",
        cause_id="private-read-message",
        operation=operation,
    )

    required = await verification.authorize_or_challenge(request)

    assert required.status == "verification_required"
    assert required.masked_destination == "j***@example.com"
    assert required.expires_at == now + timedelta(minutes=15)
    assert required.authorization_expires_at is None

    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
        notification_clock=lambda: now,
    )
    notification = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="verification-email-worker",
            lease_duration=timedelta(minutes=5),
            kinds=("verification_code_email",),
        )
    )
    assert notification is not None
    assert notification.kind == "verification_code_email"
    delivery = await verification.read_email_delivery(
        notification_id=notification.notification_id,
        workflow_event_id=notification.workflow_event_id,
        workflow_id=notification.workflow_id,
        worker_id="verification-email-worker",
        delivery_attempt=notification.delivery_attempt,
    )
    assert delivery.destination == "john@example.com"
    assert len(delivery.code) == 6
    assert delivery.code.isdigit()

    await database.dispose()
    restarted_database = WorkflowDatabase(migrated_postgres_url)
    restarted_verification = StepUpVerification(
        database=restarted_database,
        code_secret=b"verification-test-secret",
        clock=lambda: now,
    )
    verified = await restarted_verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="verification-code-message",
            code=delivery.code,
        )
    )

    assert verified.status == "verified"
    assert verified.operation == operation
    assert verified.workflow_id == TARGET_ID
    assert verified.request_cause_id == "private-read-message"
    assert verified.authorization_expires_at == now + timedelta(minutes=15)

    authorized = await restarted_verification.authorize_or_challenge(
        request.model_copy(update={"cause_id": "private-read-retry"})
    )
    assert authorized.status == "authorized"
    assert authorized.challenge_id == verified.challenge_id
    assert authorized.authorization_expires_at == now + timedelta(minutes=15)

    await restarted_database.dispose()


async def test_verification_email_uses_durable_notification_worker(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-worker-secret",
    )
    await verification.authorize_or_challenge(
        AuthorizeProtectedOperationCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            workflow_id=TARGET_ID,
            purpose="sensitive_read",
            cause_id="private-read-message",
            operation=ProtectedOperation(
                name="read_workflow_packet",
                arguments={"workflow_id": str(TARGET_ID)},
            ),
        )
    )
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    sender = DeterministicVerificationEmailSender()
    worker = NotificationWorker(
        control_plane=control_plane,
        interactions=VerificationEmailInteractionFactory(
            verification=verification,
            sender=sender,
        ),
        worker_id="verification-email-worker",
        notification_kinds=("verification_code_email",),
    )

    delivered = await worker.run_once()

    assert delivered is not None
    assert delivered.kind == "verification_code_email"
    assert len(sender.deliveries) == 1
    assert sender.deliveries[0].destination == "john@example.com"
    assert sender.deliveries[0].code.isdigit()
    assert len(sender.deliveries[0].code) == 6
    await database.dispose()


async def test_delivery_unavailable_does_not_create_an_unreachable_challenge(
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

    decision = await verification.authorize_or_challenge(
        AuthorizeProtectedOperationCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            workflow_id=TARGET_ID,
            purpose="sensitive_read",
            cause_id="private-read-message",
            operation=ProtectedOperation(
                name="read_workflow_packet",
                arguments={"workflow_id": str(TARGET_ID)},
            ),
        )
    )

    assert decision.status == "verification_unavailable"
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    assert (
        await control_plane.claim_notification(
            ClaimNotificationCommand(
                worker_id="verification-email-worker",
                lease_duration=timedelta(minutes=5),
                kinds=("verification_code_email",),
            )
        )
        is None
    )
    await database.dispose()


async def test_exhausted_email_delivery_notifies_waiting_interaction(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    current_time = [datetime(2026, 7, 13, 12, 0, tzinfo=UTC)]
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-delivery-failure-secret",
        clock=lambda: current_time[0],
    )
    request = AuthorizeProtectedOperationCommand(
        actor_party_id=JOHN_ACME_ID,
        interaction_id="sms-policyholder-demo",
        workflow_id=TARGET_ID,
        purpose="sensitive_read",
        cause_id="private-read-message",
        operation=ProtectedOperation(
            name="read_workflow_packet",
            arguments={"workflow_id": str(TARGET_ID)},
        ),
    )
    required = await verification.authorize_or_challenge(request)

    class FailingSender:
        async def send(self, _delivery) -> None:
            raise RuntimeError("provider unavailable")

    notices: list[tuple[str, str]] = []
    worker = NotificationWorker(
        control_plane=WorkflowControlPlane(
            database=database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(grants=set()),
            notification_clock=lambda: current_time[0],
        ),
        interactions=VerificationEmailInteractionFactory(
            verification=verification,
            sender=FailingSender(),
        ),
        worker_id="verification-email-worker",
        notification_kinds=("verification_code_email",),
        on_delivery_failure=VerificationDeliveryFailureHandler(
            verification=verification,
            notify=lambda interaction_id, message: notices.append((interaction_id, message)),
        ),
    )

    for advance in (timedelta(seconds=2), timedelta(seconds=3), timedelta(0)):
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await worker.run_once()
        current_time[0] += advance

    assert notices == [
        (
            "sms-policyholder-demo",
            VerificationDeliveryFailureHandler.MESSAGE,
        )
    ]
    replacement = await verification.authorize_or_challenge(
        request.model_copy(update={"cause_id": "retry-private-read"})
    )
    assert replacement.status == "verification_required"
    assert replacement.challenge_id != required.challenge_id
    await database.dispose()


async def test_verification_is_single_use_scope_bound_and_expires(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    current_time = [datetime(2026, 7, 13, 12, 0, tzinfo=UTC)]
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-scope-secret",
        clock=lambda: current_time[0],
    )
    read_request = AuthorizeProtectedOperationCommand(
        actor_party_id=JOHN_ACME_ID,
        interaction_id="sms-policyholder-demo",
        workflow_id=TARGET_ID,
        purpose="sensitive_read",
        cause_id="private-read-message",
        operation=ProtectedOperation(
            name="read_workflow_packet",
            arguments={"workflow_id": str(TARGET_ID)},
        ),
    )
    required = await verification.authorize_or_challenge(read_request)
    assert required.challenge_id is not None
    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
        notification_clock=lambda: current_time[0],
    )
    notification = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="verification-email-worker",
            lease_duration=timedelta(minutes=5),
            kinds=("verification_code_email",),
        )
    )
    assert notification is not None
    delivery = await verification.read_email_delivery(
        notification_id=notification.notification_id,
        workflow_event_id=notification.workflow_event_id,
        workflow_id=notification.workflow_id,
        worker_id="verification-email-worker",
        delivery_attempt=notification.delivery_attempt,
    )
    submission = SubmitVerificationCodeCommand(
        actor_party_id=JOHN_ACME_ID,
        interaction_id="sms-policyholder-demo",
        cause_id="verification-code-message",
        code=delivery.code,
    )

    verified = await verification.submit_code(submission)
    replay = await verification.submit_code(submission)
    reused_code = await verification.submit_code(
        submission.model_copy(update={"cause_id": "different-code-message"})
    )

    assert verified.status == "verified"
    assert replay == verified
    assert reused_code.status == "no_active_challenge"
    assert (await verification.authorize_or_challenge(read_request)).status == "authorized"

    write_required = await verification.authorize_or_challenge(
        read_request.model_copy(
            update={
                "purpose": "sensitive_write",
                "cause_id": "private-write-message",
                "operation": ProtectedOperation(
                    name="approve_job",
                    arguments={
                        "job_id": "60000000-0000-0000-0000-000000000001",
                        "expected_draft_revision_id": ("60000000-0000-0000-0000-000000000002"),
                    },
                ),
            }
        )
    )
    assert write_required.status == "verification_required"
    assert write_required.challenge_id != required.challenge_id
    second_notification = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="verification-email-worker-2",
            lease_duration=timedelta(minutes=5),
            kinds=("verification_code_email",),
        )
    )
    assert second_notification is not None
    second_delivery = await verification.read_email_delivery(
        notification_id=second_notification.notification_id,
        workflow_event_id=second_notification.workflow_event_id,
        workflow_id=second_notification.workflow_id,
        worker_id="verification-email-worker-2",
        delivery_attempt=second_notification.delivery_attempt,
    )
    current_time[0] += timedelta(minutes=16)
    expired = await verification.submit_code(
        SubmitVerificationCodeCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            cause_id="expired-code-message",
            code=second_delivery.code,
        )
    )
    assert expired.status == "expired"
    await database.dispose()


async def test_new_pending_operation_supersedes_old_email_delivery(
    migrated_postgres_url: str,
    clean_workflow_database,
):
    await seed_retrieval_landscape(migrated_postgres_url)
    database = WorkflowDatabase(migrated_postgres_url)
    verification = StepUpVerification(
        database=database,
        code_secret=b"verification-supersession-secret",
    )
    first = await verification.authorize_or_challenge(
        AuthorizeProtectedOperationCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            workflow_id=TARGET_ID,
            purpose="sensitive_read",
            cause_id="first-private-read",
            operation=ProtectedOperation(
                name="read_workflow_packet",
                arguments={"workflow_id": str(TARGET_ID)},
            ),
        )
    )
    second = await verification.authorize_or_challenge(
        AuthorizeProtectedOperationCommand(
            actor_party_id=JOHN_ACME_ID,
            interaction_id="sms-policyholder-demo",
            workflow_id=TARGET_ID,
            purpose="sensitive_read",
            cause_id="replacement-private-read",
            operation=ProtectedOperation(
                name="read_workflow_packet",
                arguments={"workflow_id": str(TARGET_ID), "view": "replacement"},
            ),
        )
    )
    assert first.challenge_id != second.challenge_id

    control_plane = WorkflowControlPlane(
        database=database,
        registry=default_workflow_registry(),
        authority=StaticWorkflowAuthority(grants=set()),
    )
    claimed = await control_plane.claim_notification(
        ClaimNotificationCommand(
            worker_id="verification-email-worker",
            lease_duration=timedelta(minutes=5),
            kinds=("verification_code_email",),
        )
    )
    assert claimed is not None
    delivery = await verification.read_email_delivery(
        notification_id=claimed.notification_id,
        workflow_event_id=claimed.workflow_event_id,
        workflow_id=claimed.workflow_id,
        worker_id="verification-email-worker",
        delivery_attempt=claimed.delivery_attempt,
    )
    assert delivery.challenge_id == second.challenge_id
    await database.dispose()
