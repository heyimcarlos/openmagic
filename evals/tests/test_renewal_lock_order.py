from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Literal
from uuid import UUID, uuid4

from example_insurance.migrations import apply_migrations
from example_insurance.renewals import (
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    ApproveRenewalDraftResult,
    CancelRenewalOutreach,
    CancelRenewalOutreachInput,
    CancelRenewalOutreachResult,
    ExampleInsurance,
    RenewalFacts,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
    WorkflowAttemptResult,
)
from openmagic_evals.harness import (
    LocalEmailProvider,
    approve_renewal,
    prepare_renewal_approval,
    renewal_context,
)
from openmagic_evals.harness._postgres import postgres_container
from openmagic_runtime.commands import Actor, Cause, CommandReceipt
from openmagic_runtime.execution import ExecutionAuthorityLost
from openmagic_runtime.kernel.work import ClaimedAttempt, StaleAuthority
from openmagic_runtime.threads import CreateThread, ThreadStore


def _approval_command(
    application: ExampleInsurance,
    workflow_id: UUID,
    actor: Actor,
) -> ApproveRenewalDraft:
    presentation = application.renewal_approval_presentation(workflow_id)
    return ApproveRenewalDraft(
        command_id=uuid4(),
        actor=actor,
        cause=Cause("message", str(uuid4())),
        input=ApproveRenewalDraftInput(
            workflow_id=workflow_id,
            wait_id=presentation.wait_id,
            draft_id=presentation.draft_id,
            message_id=presentation.message_id,
            thread_sequence=presentation.thread_sequence,
            message_fingerprint=presentation.message_fingerprint,
            presentation_fingerprint=presentation.presentation_fingerprint,
            proposed_effect=presentation.proposed_effect,
        ),
    )


def _cancellation(workflow_id: UUID, actor: Actor) -> CancelRenewalOutreach:
    return CancelRenewalOutreach(
        command_id=uuid4(),
        actor=actor,
        cause=Cause("message", str(uuid4())),
        input=CancelRenewalOutreachInput(workflow_id),
    )


def _start_renewal(
    application: ExampleInsurance,
    threads: ThreadStore,
) -> tuple[StartRenewalOutreach, Actor]:
    thread = threads.create(CreateThread(uuid4(), "email", f"broker-{uuid4()}"))
    actor = Actor("party", str(uuid4()))
    command = StartRenewalOutreach(
        command_id=uuid4(),
        actor=actor,
        cause=Cause("message", str(uuid4())),
        input=StartRenewalOutreachInput(
            workflow_id=uuid4(),
            thread_id=thread.thread_id,
            policy_id=uuid4(),
            policy_number="OM-69-LOCK",
            policyholder_name="Avery Chen",
            policyholder_email="avery@example.test",
            renewal_date="2027-12-31",
            expiring_premium_cents=250_000,
        ),
    )
    application.replace_renewal_facts(
        RenewalFacts(
            policy_id=command.input.policy_id,
            policy_number=command.input.policy_number,
            policyholder_name=command.input.policyholder_name,
            policyholder_email=command.input.policyholder_email,
            renewal_date=command.input.renewal_date,
            expiring_premium_cents=command.input.expiring_premium_cents,
        )
    )
    application.start_renewal_outreach(command)
    return command, actor


def _approve_after_barrier(
    barrier: Barrier,
    application: ExampleInsurance,
    command: ApproveRenewalDraft,
) -> CommandReceipt[ApproveRenewalDraftResult]:
    barrier.wait()
    return application.approve_renewal_draft(command)


def _cancel_after_barrier(
    barrier: Barrier,
    application: ExampleInsurance,
    command: CancelRenewalOutreach,
) -> CommandReceipt[CancelRenewalOutreachResult]:
    barrier.wait()
    return application.cancel_renewal_outreach(command)


def _complete_or_authority_lost_after_barrier(
    barrier: Barrier,
    application: ExampleInsurance,
    attempt: ClaimedAttempt,
    worker_id: str,
) -> WorkflowAttemptResult | Literal["authority_lost"]:
    barrier.wait()
    try:
        return application.complete_workflow_attempt(attempt=attempt, worker_id=worker_id)
    except (ExecutionAuthorityLost, StaleAuthority):
        return "authority_lost"


def _recover_after_barrier(barrier: Barrier, application: ExampleInsurance) -> bool:
    barrier.wait()
    return application.recover_expired_workflow_attempt()


def _complete_after_barrier(
    barrier: Barrier,
    application: ExampleInsurance,
    attempt: ClaimedAttempt,
    worker_id: str,
) -> WorkflowAttemptResult:
    barrier.wait()
    return application.complete_workflow_attempt(attempt=attempt, worker_id=worker_id)


def test_concurrent_approval_and_cancellation_complete_without_deadlock() -> None:
    with renewal_context() as (_, application, threads):
        command, actor = prepare_renewal_approval(
            application,
            threads,
        )
        approval = _approval_command(application, command.input.workflow_id, actor)
        cancellation = _cancellation(command.input.workflow_id, actor)
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as executor:
            approval_future = executor.submit(
                _approve_after_barrier,
                barrier,
                application,
                approval,
            )
            cancellation_future = executor.submit(
                _cancel_after_barrier,
                barrier,
                application,
                cancellation,
            )
            approval_result = approval_future.result(timeout=10)
            cancellation_result = cancellation_future.result(timeout=10)

        assert approval_result.result.outcome in {"approved", "authority_revoked"}
        assert cancellation_result.result.outcome == "cancelled"


def test_concurrent_attempt_acceptance_and_cancellation_complete_without_deadlock() -> None:
    with renewal_context() as (_, application, threads):
        command, actor = _start_renewal(
            application,
            threads,
        )
        attempt = application.claim_workflow_attempt(
            worker_id="facts",
            claim_request_id=uuid4(),
        )
        assert attempt is not None
        cancellation = _cancellation(command.input.workflow_id, actor)
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as executor:
            acceptance_future = executor.submit(
                _complete_or_authority_lost_after_barrier,
                barrier,
                application,
                attempt,
                "facts",
            )
            cancellation_future = executor.submit(
                _cancel_after_barrier,
                barrier,
                application,
                cancellation,
            )
            acceptance_result = acceptance_future.result(timeout=10)
            cancellation_result = cancellation_future.result(timeout=10)

        assert acceptance_result == "authority_lost" or (
            acceptance_result.template_key == "gather_renewal_facts"
        )
        assert cancellation_result.result.outcome == "cancelled"
        evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))
        assert evidence["outcomes"]["workflow_lifecycle"] == "cancelled"
        assert evidence["outcomes"]["instance_state"] == "closed"


def test_concurrent_expiry_recovery_and_cancellation_complete_without_deadlock(tmp_path) -> None:
    with (
        LocalEmailProvider(working_directory=tmp_path / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url, email_provider_url=provider.url)
        application.prepare()
        threads = ThreadStore(database_url=database_url)
        command, actor = prepare_renewal_approval(
            application,
            threads,
        )
        approve_renewal(application, command, actor)
        attempt = application.claim_workflow_attempt(
            worker_id="expired",
            claim_request_id=uuid4(),
        )
        assert attempt is not None
        time.sleep(1.1)
        cancellation = _cancellation(command.input.workflow_id, actor)
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as executor:
            recovery_future = executor.submit(
                _recover_after_barrier,
                barrier,
                application,
            )
            cancellation_future = executor.submit(
                _cancel_after_barrier,
                barrier,
                application,
                cancellation,
            )
            recovery_result = recovery_future.result(timeout=10)
            cancellation_result = cancellation_future.result(timeout=10)

        assert isinstance(recovery_result, bool)
        assert cancellation_result.result.outcome == "cancelled"


def test_concurrent_recovery_skips_a_locked_expired_instance(tmp_path) -> None:
    with (
        LocalEmailProvider(working_directory=tmp_path / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url, email_provider_url=provider.url)
        application.prepare()
        threads = ThreadStore(database_url=database_url)
        first_command, first_actor = prepare_renewal_approval(application, threads)
        second_command, second_actor = prepare_renewal_approval(application, threads)
        approve_renewal(application, first_command, first_actor)
        approve_renewal(application, second_command, second_actor)
        first_attempt = application.claim_workflow_attempt(
            worker_id="expired-first",
            claim_request_id=uuid4(),
        )
        second_attempt = application.claim_workflow_attempt(
            worker_id="expired-second",
            claim_request_id=uuid4(),
        )
        assert first_attempt is not None
        assert second_attempt is not None
        assert first_attempt.instance_id != second_attempt.instance_id
        time.sleep(1.1)
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_recovery = executor.submit(
                _recover_after_barrier,
                barrier,
                application,
            )
            second_recovery = executor.submit(
                _recover_after_barrier,
                barrier,
                application,
            )
            first_result = first_recovery.result(timeout=10)
            second_result = second_recovery.result(timeout=10)

        assert first_result is True
        assert second_result is True


def test_concurrent_completion_and_too_late_cancellation_complete_without_deadlock(
    tmp_path,
) -> None:
    with (
        LocalEmailProvider(working_directory=tmp_path / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url, email_provider_url=provider.url)
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=database_url),
        )
        approve_renewal(application, command, actor)
        attempt = application.claim_workflow_attempt(
            worker_id="email",
            claim_request_id=uuid4(),
        )
        assert attempt is not None
        application.authorize_email_dispatch(attempt=attempt, worker_id="email")
        cancellation = _cancellation(command.input.workflow_id, actor)
        barrier = Barrier(2)

        with ThreadPoolExecutor(max_workers=2) as executor:
            completion_future = executor.submit(
                _complete_after_barrier,
                barrier,
                application,
                attempt,
                "email",
            )
            cancellation_future = executor.submit(
                _cancel_after_barrier,
                barrier,
                application,
                cancellation,
            )
            completion_result = completion_future.result(timeout=10)
            cancellation_result = cancellation_future.result(timeout=10)

        assert completion_result.template_key == "send_renewal_email"
        assert cancellation_result.result.outcome == "too_late"
