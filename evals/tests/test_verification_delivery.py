from __future__ import annotations

import re
import time
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
from openmagic_runtime.delivery import DeliveryWork
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
