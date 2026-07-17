from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from uuid import uuid4

import psycopg
import pytest
from example_insurance.renewals import (
    ExampleInsurance,
    RequestProtectedRenewalDetails,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
)
from openmagic_evals.harness import (
    issue_verification_challenge,
    renewal_context,
)
from openmagic_runtime.commands import Cause
from openmagic_runtime.delivery import DeliveryWork, StaleDeliveryAuthority
from openmagic_runtime.kernel.inspection import KernelInspection


def test_verification_delivery_failure_does_not_mutate_protected_workflow() -> None:
    with renewal_context(verification_code_secret=b"issue-70-delivery-failure") as (
        database_url,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads, deliver=False)
        renewal = scenario.renewal
        actor = scenario.actor
        protected = scenario.protected_command
        required = scenario.challenge_receipt
        assert required.result.challenge_id is not None
        assert required.result.verification_instance_id is not None
        renewal_before = KernelInspection(database_url=database_url).snapshot(
            application.start_renewal_outreach(renewal).result.instance_id
        )
        evidence_before = application.renewal_evidence_json(renewal.input.workflow_id)
        claim = application.claim_delivery_attempt(
            worker_id="failed-verification-delivery",
            claim_request_id=uuid4(),
        )
        assert claim is not None
        with psycopg.connect(database_url) as connection, connection.transaction():
            failure = DeliveryWork(connection).report_failure(
                claim,
                worker_id="failed-verification-delivery",
                failure_class="policy_rejected",
            )
        replacement = application.request_protected_renewal_details(
            RequestProtectedRenewalDetails(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=protected.input,
            )
        )
        rejected = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=required.result.challenge_id,
                    protected_command_id=protected.command_id,
                    workflow_id=renewal.input.workflow_id,
                    thread_id=renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code="000000",
                ),
            )
        )
        renewal_after = KernelInspection(database_url=database_url).snapshot(
            renewal_before.instance_id
        )
        verification = KernelInspection(database_url=database_url).snapshot(
            required.result.verification_instance_id
        )
        evidence_after = application.renewal_evidence_json(renewal.input.workflow_id)

        assert failure.outcome == "failed"
        assert replacement.result.outcome == "verification_required"
        assert replacement.result.challenge_id != required.result.challenge_id
        assert rejected.result.verification_outcome == "delivery_failed"
        assert renewal_after == renewal_before
        assert evidence_after == evidence_before
        assert verification.state == "closed"


def test_verification_delivery_retry_recovers_through_fresh_attempt() -> None:
    secret = b"issue-70-delivery-recovery"
    with renewal_context(verification_code_secret=secret) as (
        database_url,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads, deliver=False)
        renewal = scenario.renewal
        actor = scenario.actor
        protected = scenario.protected_command
        required = scenario.challenge_receipt
        assert required.result.challenge_id is not None
        evidence_before = application.renewal_evidence_json(renewal.input.workflow_id)
        first = application.claim_delivery_attempt(
            worker_id="first-verification-delivery",
            claim_request_id=uuid4(),
        )
        assert first is not None
        with psycopg.connect(database_url) as connection, connection.transaction():
            retry = DeliveryWork(connection).report_failure(
                first,
                worker_id="first-verification-delivery",
                failure_class="transient_rendering",
            )
        second = application.claim_delivery_attempt(
            worker_id="recovered-verification-delivery",
            claim_request_id=uuid4(),
        )
        assert second is not None
        acknowledged = application.complete_delivery_attempt(
            claim=second,
            worker_id="recovered-verification-delivery",
        )
        evidence_after_recovery = application.renewal_evidence_json(renewal.input.workflow_id)
        code_match = re.search(
            r"\b(\d{6})\b",
            threads.read(scenario.identifier_thread_id).messages[-1].content,
        )
        assert code_match is not None
        accepted = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=required.result.challenge_id,
                    protected_command_id=protected.command_id,
                    workflow_id=renewal.input.workflow_id,
                    thread_id=renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code=code_match.group(1),
                ),
            )
        )

        assert retry.outcome == "retry_scheduled"
        assert second.attempt_number == first.attempt_number + 1
        assert acknowledged.thread_id == scenario.identifier_thread_id
        assert evidence_after_recovery == evidence_before
        assert accepted.result.verification_outcome == "verified"


def test_delivery_acknowledgement_and_failure_share_one_lock_order() -> None:
    with renewal_context(verification_code_secret=b"issue-71-delivery-lock-order") as (
        database_url,
        application,
        threads,
    ):
        issue_verification_challenge(application, threads, deliver=False)
        claim = application.claim_delivery_attempt(
            worker_id="delivery-lock-order-worker",
            claim_request_id=uuid4(),
        )
        assert claim is not None
        failure_pids: Queue[int] = Queue()

        def fail() -> str:
            with psycopg.connect(database_url) as connection, connection.transaction():
                failure_pids.put(connection.info.backend_pid)
                try:
                    DeliveryWork(connection).report_failure(
                        claim,
                        worker_id="delivery-lock-order-worker",
                        failure_class="policy_rejected",
                    )
                except StaleDeliveryAuthority:
                    return "stale"
            return "failed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            with (
                psycopg.connect(database_url) as acknowledgement_connection,
                acknowledgement_connection.transaction(),
            ):
                locked = acknowledgement_connection.execute(
                    "SELECT delivery_id FROM openmagic_runtime.deliveries "
                    "WHERE delivery_id = %s FOR UPDATE",
                    (claim.delivery_id,),
                ).fetchone()
                assert locked is not None
                failure = executor.submit(fail)
                failure_pid = failure_pids.get(timeout=5)
                acknowledgement_pid = acknowledgement_connection.info.backend_pid
                with psycopg.connect(database_url, autocommit=True) as observer:
                    deadline = time.monotonic() + 5
                    blocked_by_acknowledgement = False
                    while time.monotonic() < deadline:
                        blocked = observer.execute(
                            "SELECT %s = ANY(pg_blocking_pids(%s))",
                            (acknowledgement_pid, failure_pid),
                        ).fetchone()
                        blocked_by_acknowledgement = blocked is not None and bool(blocked[0])
                        if blocked_by_acknowledgement:
                            break
                        time.sleep(0.01)
                assert blocked_by_acknowledgement
                acknowledgement = DeliveryWork(acknowledgement_connection).acknowledge(
                    claim,
                    worker_id="delivery-lock-order-worker",
                    proposed_thread_id=claim.thread_id,
                )
            failure_outcome = failure.result(timeout=10)

        assert acknowledgement.delivery_attempt_id == claim.delivery_attempt_id
        assert failure_outcome == "stale"


def test_public_observation_submission_uses_the_verification_attempt_route() -> None:
    with renewal_context(verification_code_secret=b"issue-70-public-attempt-route") as (
        _,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads, run_workflow=False)
        challenge_id = scenario.challenge_receipt.result.challenge_id
        assert challenge_id is not None
        claimed = application.claim_workflow_attempt(
            worker_id="public-route-worker",
            claim_request_id=uuid4(),
        )
        assert claimed is not None

        result = application.submit_workflow_observation(
            attempt=claimed,
            worker_id="public-route-worker",
            observation={"challenge_id": str(challenge_id)},
        )
        delivered = application.run_delivery_worker_once(worker_id="public-route-delivery")

        assert result.template_key == "deliver_verification_challenge"
        assert delivered is not None
        assert delivered.thread_id == scenario.identifier_thread_id
        assert threads.read(scenario.identifier_thread_id).messages


def test_queued_verification_expiry_closes_without_emitting_a_delivery() -> None:
    with renewal_context(
        verification_code_secret=b"issue-70-queued-expiry",
        challenge_ttl_seconds=1,
    ) as (database_url, application, threads):
        scenario = issue_verification_challenge(application, threads, run_workflow=False)
        verification_instance_id = scenario.challenge_receipt.result.verification_instance_id
        challenge_id = scenario.challenge_receipt.result.challenge_id
        assert verification_instance_id is not None
        assert challenge_id is not None
        protected_instance_id = application.start_renewal_outreach(
            scenario.renewal
        ).result.instance_id
        protected_before = KernelInspection(database_url=database_url).snapshot(
            protected_instance_id
        )
        evidence_before = application.renewal_evidence_json(scenario.renewal.input.workflow_id)
        time.sleep(1.05)

        worker_result = application.run_workflow_worker_once(worker_id="expired-queue-worker")
        delivery = application.run_delivery_worker_once(worker_id="expired-queue-delivery")
        terminal = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=scenario.actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=challenge_id,
                    protected_command_id=scenario.protected_command.command_id,
                    workflow_id=scenario.renewal.input.workflow_id,
                    thread_id=scenario.renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code="000000",
                ),
            )
        )

        assert worker_result is not None
        assert worker_result.template_key == "deliver_verification_challenge"
        assert terminal.result.verification_outcome == "expired"
        assert terminal.result.protected_outcome is None
        assert delivery is None
        assert threads.read(scenario.identifier_thread_id).messages == ()
        assert (
            KernelInspection(database_url=database_url).snapshot(verification_instance_id).state
            == "closed"
        )
        assert (
            KernelInspection(database_url=database_url).snapshot(protected_instance_id)
            == protected_before
        )
        assert (
            application.renewal_evidence_json(scenario.renewal.input.workflow_id) == evidence_before
        )


def test_verification_attempt_recovers_after_worker_loss_from_fresh_application() -> None:
    secret = b"issue-70-attempt-restart"
    with renewal_context(verification_code_secret=secret) as (
        database_url,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(
            application,
            threads,
            run_workflow=False,
        )
        renewal = scenario.renewal
        actor = scenario.actor
        protected = scenario.protected_command
        required = scenario.challenge_receipt
        assert required.result.challenge_id is not None
        lost = application.claim_workflow_attempt(
            worker_id="lost-verification-worker",
            claim_request_id=uuid4(),
        )
        assert lost is not None
        assert lost.template_key == "deliver_verification_challenge"
        without_support = ExampleInsurance(database_url=database_url)
        without_support.prepare()
        with pytest.raises(
            RuntimeError,
            match="Open verification Workflows require deterministic executor support",
        ):
            without_support.prepare_workflow_worker()
        time.sleep(1.05)

        restarted = ExampleInsurance(
            database_url=database_url,
            verification_code_secret=secret,
        )
        restarted.prepare()
        assert restarted.recover_expired_workflow_attempt() is True
        recovered = restarted.claim_workflow_attempt(
            worker_id="restarted-verification-worker",
            claim_request_id=uuid4(),
        )
        assert recovered is not None
        assert recovered.step_id == lost.step_id
        assert recovered.attempt_number == lost.attempt_number + 1
        restarted.complete_workflow_attempt(
            attempt=recovered,
            worker_id="restarted-verification-worker",
        )
        restarted.run_delivery_worker_once(worker_id="restarted-delivery-worker")
        code_match = re.search(
            r"\b(\d{6})\b",
            threads.read(scenario.identifier_thread_id).messages[-1].content,
        )
        assert code_match is not None
        accepted = restarted.submit_verification_code(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=required.result.challenge_id,
                    protected_command_id=protected.command_id,
                    workflow_id=renewal.input.workflow_id,
                    thread_id=renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code=code_match.group(1),
                ),
            )
        )
        assert accepted.result.verification_outcome == "verified"


def test_verification_attempt_exhaustion_closes_only_its_instance() -> None:
    secret = b"issue-70-attempt-exhaustion"
    with renewal_context(verification_code_secret=secret) as (
        database_url,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads, run_workflow=False)
        verification_instance_id = scenario.challenge_receipt.result.verification_instance_id
        assert verification_instance_id is not None
        protected_instance_id = application.start_renewal_outreach(
            scenario.renewal
        ).result.instance_id
        protected_before = KernelInspection(database_url=database_url).snapshot(
            protected_instance_id
        )
        evidence_before = application.renewal_evidence_json(scenario.renewal.input.workflow_id)

        for attempt_number in range(1, 4):
            claimed = application.claim_workflow_attempt(
                worker_id=f"lost-verification-worker-{attempt_number}",
                claim_request_id=uuid4(),
            )
            assert claimed is not None
            assert claimed.attempt_number == attempt_number
            time.sleep(1.05)
            assert application.recover_expired_workflow_attempt() is True

        verification_after = KernelInspection(database_url=database_url).snapshot(
            verification_instance_id
        )
        protected_after = KernelInspection(database_url=database_url).snapshot(
            protected_instance_id
        )

        assert verification_after.state == "closed"
        assert verification_after.steps[0].state == "failed"
        assert protected_after == protected_before
        assert (
            application.renewal_evidence_json(scenario.renewal.input.workflow_id) == evidence_before
        )
        ExampleInsurance(database_url=database_url).prepare_workflow_worker()
