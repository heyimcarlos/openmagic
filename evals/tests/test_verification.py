from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from example_insurance.renewals import (
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    CancelRenewalOutreach,
    CancelRenewalOutreachInput,
    ExampleInsurance,
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityInput,
    RequestProtectedRenewalDetails,
    RequestProtectedRenewalDetailsInput,
    RevokeVerificationAuthority,
    RevokeVerificationAuthorityInput,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
    VerificationAuthorityTarget,
)
from openmagic_evals.harness import TestDeployment, prepare_renewal_approval, renewal_context
from openmagic_runtime.commands import Actor, Cause, IdempotencyConflict
from openmagic_runtime.delivery import DeliveryWork
from openmagic_runtime.evidence import RuntimeEvidenceReader
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.threads import ThreadStore


def _issue_challenge(application, threads, *, run_workflow: bool = True, deliver: bool = True):
    renewal, actor = prepare_renewal_approval(application, threads)
    presentation = application.renewal_approval_presentation(renewal.input.workflow_id)
    approval = application.approve_renewal_draft(
        ApproveRenewalDraft(
            command_id=uuid4(),
            actor=actor,
            cause=Cause("message", str(uuid4())),
            input=ApproveRenewalDraftInput(
                workflow_id=renewal.input.workflow_id,
                wait_id=presentation.wait_id,
                draft_id=presentation.draft_id,
                message_id=presentation.message_id,
                thread_sequence=presentation.thread_sequence,
                message_fingerprint=presentation.message_fingerprint,
                presentation_fingerprint=presentation.presentation_fingerprint,
                proposed_effect=presentation.proposed_effect,
            ),
        )
    )
    assert approval.result.approval_grant_id is not None
    party_id = UUID(actor.identifier)
    application.provision_verification_authority(
        ProvisionVerificationAuthority(
            command_id=uuid4(),
            actor=Actor("system", "fixture-authority"),
            cause=Cause("command", str(uuid4())),
            input=ProvisionVerificationAuthorityInput(
                party_id=party_id,
                organization_party_id=uuid4(),
                workflow_id=renewal.input.workflow_id,
                email=f"broker-{party_id}@example.test",
            ),
        )
    )
    protected = RequestProtectedRenewalDetails(
        command_id=uuid4(),
        actor=actor,
        cause=Cause("message", str(uuid4())),
        input=RequestProtectedRenewalDetailsInput(
            workflow_id=renewal.input.workflow_id,
            thread_id=renewal.input.thread_id,
            purpose="renewal.read_approved_details",
            approval_grant_id=approval.result.approval_grant_id,
        ),
    )
    required = application.request_protected_renewal_details(protected)
    assert required.result.challenge_id is not None
    if not run_workflow:
        return renewal, actor, protected, required, None
    application.run_workflow_worker_once(worker_id="verification-worker")
    if not deliver:
        return renewal, actor, protected, required, None
    application.run_delivery_worker_once(worker_id="verification-delivery")
    code_match = re.search(
        r"\b(\d{6})\b", threads.read(renewal.input.thread_id).messages[-1].content
    )
    assert code_match is not None
    return renewal, actor, protected, required, code_match.group(1)


def test_step_up_verification_resumes_exact_protected_command_without_mutating_renewal() -> None:
    secret = b"issue-70-public-contract-secret"
    with renewal_context(verification_code_secret=secret) as (
        database_url,
        application,
        threads,
    ):
        renewal, actor = prepare_renewal_approval(application, threads)
        renewal_receipt = application.start_renewal_outreach(renewal)
        presentation = application.renewal_approval_presentation(renewal.input.workflow_id)
        approval = application.approve_renewal_draft(
            ApproveRenewalDraft(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=ApproveRenewalDraftInput(
                    workflow_id=renewal.input.workflow_id,
                    wait_id=presentation.wait_id,
                    draft_id=presentation.draft_id,
                    message_id=presentation.message_id,
                    thread_sequence=presentation.thread_sequence,
                    message_fingerprint=presentation.message_fingerprint,
                    presentation_fingerprint=presentation.presentation_fingerprint,
                    proposed_effect=presentation.proposed_effect,
                ),
            )
        )
        assert approval.result.approval_grant_id is not None
        party_id = UUID(actor.identifier)
        organization_party_id = uuid4()
        provisioned = application.provision_verification_authority(
            ProvisionVerificationAuthority(
                command_id=uuid4(),
                actor=Actor("system", "fixture-authority"),
                cause=Cause("command", str(uuid4())),
                input=ProvisionVerificationAuthorityInput(
                    party_id=party_id,
                    organization_party_id=organization_party_id,
                    workflow_id=renewal.input.workflow_id,
                    email="broker@example.test",
                ),
            )
        )
        protected = RequestProtectedRenewalDetails(
            command_id=uuid4(),
            actor=actor,
            cause=Cause("message", str(uuid4())),
            input=RequestProtectedRenewalDetailsInput(
                workflow_id=renewal.input.workflow_id,
                thread_id=renewal.input.thread_id,
                purpose="renewal.read_approved_details",
                approval_grant_id=approval.result.approval_grant_id,
            ),
        )

        required = application.request_protected_renewal_details(protected)
        assert provisioned.result.outcome == "provisioned"
        assert required.result.outcome == "verification_required"
        assert required.result.challenge_id is not None
        assert required.result.verification_instance_id is not None
        assert application.run_workflow_worker_once(worker_id="verification-worker") is not None
        challenge_delivery = application.run_delivery_worker_once(worker_id="verification-delivery")
        assert challenge_delivery is not None
        assert (
            application.replay_delivery_acknowledgement(
                delivery_attempt_id=challenge_delivery.delivery_attempt_id
            )
            == challenge_delivery
        )
        thread = threads.read(renewal.input.thread_id)
        code_match = re.search(r"\b(\d{6})\b", thread.messages[-1].content)
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
        resumed_delivery = application.run_delivery_worker_once(worker_id="verification-resumption")
        renewal_snapshot = KernelInspection(database_url=database_url).snapshot(
            application.start_renewal_outreach(renewal).result.instance_id
        )
        verification_snapshot = KernelInspection(database_url=database_url).snapshot(
            required.result.verification_instance_id
        )

        assert accepted.result.verification_outcome == "verified"
        assert accepted.result.protected_outcome == "authorized"
        assert accepted.result.session_id is not None
        assert resumed_delivery is not None
        assert (
            application.replay_delivery_acknowledgement(
                delivery_attempt_id=resumed_delivery.delivery_attempt_id
            )
            == resumed_delivery
        )
        assert resumed_delivery.thread_id == renewal.input.thread_id
        assert renewal_snapshot.state == "open"
        assert verification_snapshot.state == "closed"
        assert application.start_renewal_outreach(renewal) == renewal_receipt


def test_verification_code_is_single_use_replay_safe_and_serialized() -> None:
    with renewal_context(verification_code_secret=b"issue-70-code-race-secret") as (
        _,
        application,
        threads,
    ):
        renewal, actor, protected, required, code = _issue_challenge(application, threads)
        assert required.result.challenge_id is not None
        assert application.request_protected_renewal_details(protected) == required
        with pytest.raises(IdempotencyConflict):
            application.request_protected_renewal_details(
                replace(protected, input=replace(protected.input, thread_id=uuid4()))
            )
        pending = application.request_protected_renewal_details(
            RequestProtectedRenewalDetails(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=protected.input,
            )
        )
        assert pending.result.outcome == "verification_in_progress"
        assert pending.result.challenge_id == required.result.challenge_id
        wrong = application.submit_verification_code(
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
                    code="000000" if code != "000000" else "999999",
                ),
            )
        )
        assert wrong.result.verification_outcome == "invalid_code"

        barrier = Barrier(2)
        commands = tuple(
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
                    code=code,
                ),
            )
            for _ in range(2)
        )

        def submit(command: SubmitVerificationCode):
            barrier.wait()
            return application.submit_verification_code(command)

        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = tuple(executor.map(submit, commands))

        assert sorted(result.result.verification_outcome for result in receipts) == [
            "already_used",
            "verified",
        ]
        winner_index = next(
            index
            for index, receipt in enumerate(receipts)
            if receipt.result.verification_outcome == "verified"
        )
        assert (
            application.submit_verification_code(commands[winner_index]) == receipts[winner_index]
        )


def test_challenge_rejects_every_wrong_binding_without_consuming_code() -> None:
    with renewal_context(verification_code_secret=b"issue-70-binding-secret") as (
        _,
        application,
        threads,
    ):
        renewal, actor, protected, required, code = _issue_challenge(application, threads)
        assert required.result.challenge_id is not None
        exact = SubmitVerificationCodeInput(
            challenge_id=required.result.challenge_id,
            protected_command_id=protected.command_id,
            workflow_id=renewal.input.workflow_id,
            thread_id=renewal.input.thread_id,
            purpose="renewal.read_approved_details",
            code=code,
        )
        submissions = (
            (Actor("party", str(uuid4())), exact, "wrong_party"),
            (actor, replace(exact, protected_command_id=uuid4()), "wrong_protected_command"),
            (actor, replace(exact, workflow_id=uuid4()), "wrong_workflow"),
            (actor, replace(exact, thread_id=uuid4()), "wrong_thread"),
            (actor, replace(exact, purpose="renewal.other_purpose"), "wrong_purpose"),
        )

        outcomes = tuple(
            application.submit_verification_code(
                SubmitVerificationCode(
                    command_id=uuid4(),
                    actor=submission_actor,
                    cause=Cause("message", str(uuid4())),
                    input=input_value,
                )
            ).result.verification_outcome
            for submission_actor, input_value, _ in submissions
        )

        assert outcomes == tuple(expected for _, _, expected in submissions)
        request_outcomes = tuple(
            application.request_protected_renewal_details(
                RequestProtectedRenewalDetails(
                    command_id=uuid4(),
                    actor=request_actor,
                    cause=Cause("message", str(uuid4())),
                    input=input_value,
                )
            ).result.outcome
            for request_actor, input_value in (
                (actor, replace(protected.input, thread_id=uuid4())),
                (actor, replace(protected.input, purpose="renewal.other_purpose")),
                (actor, replace(protected.input, approval_grant_id=uuid4())),
                (Actor("party", str(uuid4())), protected.input),
            )
        )
        assert request_outcomes == (
            "wrong_thread",
            "wrong_purpose",
            "approval_required",
            "wrong_party",
        )
        accepted = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=exact,
            )
        )
        assert accepted.result.verification_outcome == "verified"


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("identifier", "identifier_revoked"),
        ("membership", "authority_revoked"),
        ("workflow_role", "authority_revoked"),
    ],
)
def test_current_authority_is_revalidated_after_code_delivery(
    target: VerificationAuthorityTarget, expected: str
) -> None:
    with renewal_context(verification_code_secret=b"issue-70-revocation-secret") as (
        _,
        application,
        threads,
    ):
        renewal, actor, protected, required, code = _issue_challenge(application, threads)
        assert required.result.challenge_id is not None
        revoked = application.revoke_verification_authority(
            RevokeVerificationAuthority(
                command_id=uuid4(),
                actor=Actor("system", "authority-administrator"),
                cause=Cause("command", str(uuid4())),
                input=RevokeVerificationAuthorityInput(
                    party_id=UUID(actor.identifier),
                    workflow_id=renewal.input.workflow_id,
                    target=target,
                ),
            )
        )
        result = application.submit_verification_code(
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
                    code=code,
                ),
            )
        )

        assert revoked.result.outcome == "revoked"
        assert result.result.verification_outcome == expected
        assert result.result.protected_outcome == expected
        assert result.result.session_id is None


def test_expired_challenge_and_closed_workflow_fail_without_mutating_lifecycle() -> None:
    with renewal_context(
        verification_code_secret=b"issue-70-expiry-secret",
        challenge_ttl_seconds=1,
    ) as (database_url, application, threads):
        renewal, actor, protected, required, code = _issue_challenge(application, threads)
        assert required.result.challenge_id is not None
        time.sleep(1.05)
        expired_command = SubmitVerificationCode(
            command_id=uuid4(),
            actor=actor,
            cause=Cause("message", str(uuid4())),
            input=SubmitVerificationCodeInput(
                challenge_id=required.result.challenge_id,
                protected_command_id=protected.command_id,
                workflow_id=renewal.input.workflow_id,
                thread_id=renewal.input.thread_id,
                purpose="renewal.read_approved_details",
                code=code,
            ),
        )
        expired = application.submit_verification_code(expired_command)
        assert expired.result.verification_outcome == "expired"
        assert application.submit_verification_code(expired_command) == expired

        second_renewal, second_actor, second_protected, second_required, second_code = (
            _issue_challenge(application, threads)
        )
        assert second_required.result.challenge_id is not None
        cancelled = application.cancel_renewal_outreach(
            CancelRenewalOutreach(
                command_id=uuid4(),
                actor=second_actor,
                cause=Cause("message", str(uuid4())),
                input=CancelRenewalOutreachInput(second_renewal.input.workflow_id),
            )
        )
        closed = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=second_actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=second_required.result.challenge_id,
                    protected_command_id=second_protected.command_id,
                    workflow_id=second_renewal.input.workflow_id,
                    thread_id=second_renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code=second_code,
                ),
            )
        )
        snapshot = KernelInspection(database_url=database_url).snapshot(
            cancelled.result.instance_id
        )

        assert cancelled.result.outcome == "cancelled"
        assert closed.result.verification_outcome == "workflow_closed"
        assert closed.result.protected_outcome == "workflow_closed"
        assert snapshot.state == "closed"


def test_verification_delivery_failure_does_not_mutate_protected_workflow() -> None:
    with renewal_context(verification_code_secret=b"issue-70-delivery-failure") as (
        database_url,
        application,
        threads,
    ):
        renewal, actor, protected, required, _ = _issue_challenge(
            application, threads, deliver=False
        )
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
        assert rejected.result.verification_outcome == "delivery_unconfirmed"
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
        renewal, actor, protected, required, _ = _issue_challenge(
            application, threads, deliver=False
        )
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
            r"\b(\d{6})\b", threads.read(renewal.input.thread_id).messages[-1].content
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
        assert acknowledged.thread_id == renewal.input.thread_id
        assert evidence_after_recovery == evidence_before
        assert accepted.result.verification_outcome == "verified"


def test_session_reuses_only_same_party_and_thread_then_expires() -> None:
    with renewal_context(
        verification_code_secret=b"issue-70-session-expiry",
        session_ttl_seconds=1,
    ) as (_, application, threads):
        renewal, actor, protected, required, code = _issue_challenge(application, threads)
        assert required.result.challenge_id is not None
        verified = application.submit_verification_code(
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
                    code=code,
                ),
            )
        )
        reused = application.request_protected_renewal_details(
            RequestProtectedRenewalDetails(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=protected.input,
            )
        )
        wrong_thread = application.request_protected_renewal_details(
            RequestProtectedRenewalDetails(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=replace(protected.input, thread_id=uuid4()),
            )
        )
        time.sleep(1.05)
        expired_session = application.request_protected_renewal_details(
            RequestProtectedRenewalDetails(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=protected.input,
            )
        )

        assert verified.result.verification_outcome == "verified"
        assert reused.result.outcome == "authorized"
        assert reused.result.challenge_id is None
        assert reused.result.authorized_delivery_id is not None
        assert wrong_thread.result.outcome == "wrong_thread"
        assert expired_session.result.outcome == "verification_required"
        assert expired_session.result.challenge_id is not None


def test_verification_attempt_recovers_after_worker_loss_from_fresh_application() -> None:
    secret = b"issue-70-attempt-restart"
    with renewal_context(verification_code_secret=secret) as (
        database_url,
        application,
        threads,
    ):
        renewal, actor, protected, required, _ = _issue_challenge(
            application,
            threads,
            run_workflow=False,
        )
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
            r"\b(\d{6})\b", threads.read(renewal.input.thread_id).messages[-1].content
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


def test_process_termination_restart_reconstructs_verification_from_postgresql(
    tmp_path: Path,
) -> None:
    secret = "issue-70-separate-process-secret"
    with TestDeployment(
        working_directory=tmp_path,
        verification_code_secret=secret,
    ) as deployment:
        stopped_workflow = deployment.terminate_role("workflow-worker")
        stopped_delivery = deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            verification_code_secret=secret.encode(),
        )
        application.prepare()
        threads = ThreadStore(database_url=deployment.database_url)
        renewal, actor, protected, required, _ = _issue_challenge(
            application,
            threads,
            run_workflow=False,
        )
        assert required.result.challenge_id is not None
        assert required.result.verification_instance_id is not None

        restarted_workflow = deployment.restart_role("workflow-worker")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (
                KernelInspection(database_url=deployment.database_url)
                .snapshot(required.result.verification_instance_id)
                .state
                == "closed"
            ):
                break
            time.sleep(0.02)
        else:
            raise AssertionError("Restarted Workflow Worker did not complete verification")

        restarted_delivery = deployment.restart_role("delivery-worker")
        deadline = time.monotonic() + 10
        code_match = None
        while time.monotonic() < deadline:
            messages = threads.read(renewal.input.thread_id).messages
            code_match = re.search(r"\b(\d{6})\b", messages[-1].content)
            if code_match is not None:
                break
            time.sleep(0.02)
        assert code_match is not None
        deployment.terminate_role("delivery-worker")

        payload = {
            "database_url": deployment.database_url,
            "secret": secret,
            "command_id": str(uuid4()),
            "party_id": actor.identifier,
            "cause_id": str(uuid4()),
            "challenge_id": str(required.result.challenge_id),
            "protected_command_id": str(protected.command_id),
            "workflow_id": str(renewal.input.workflow_id),
            "thread_id": str(renewal.input.thread_id),
            "code": code_match.group(1),
        }
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                """
import json
import sys
from uuid import UUID
from example_insurance.renewals import ExampleInsurance, SubmitVerificationCode, SubmitVerificationCodeInput
from openmagic_runtime.commands import Actor, Cause

value = json.load(sys.stdin)
application = ExampleInsurance(
    database_url=value["database_url"],
    verification_code_secret=value["secret"].encode(),
)
application.prepare()
receipt = application.submit_verification_code(
    SubmitVerificationCode(
        command_id=UUID(value["command_id"]),
        actor=Actor("party", value["party_id"]),
        cause=Cause("message", value["cause_id"]),
        input=SubmitVerificationCodeInput(
            challenge_id=UUID(value["challenge_id"]),
            protected_command_id=UUID(value["protected_command_id"]),
            workflow_id=UUID(value["workflow_id"]),
            thread_id=UUID(value["thread_id"]),
            purpose="renewal.read_approved_details",
            code=value["code"],
        ),
    )
)
print(json.dumps({"outcome": receipt.result.verification_outcome}))
""",
            ],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
            cwd=tmp_path,
            env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"},
        )
        child_result = json.loads(child.stdout)
        resumed_delivery = deployment.restart_role("delivery-worker")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            messages = threads.read(renewal.input.thread_id).messages
            if "Approved renewal details" in messages[-1].content:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("Restarted Delivery Worker did not resume exact Thread")

        assert restarted_workflow.pid != stopped_workflow.pid
        assert restarted_delivery.pid != stopped_delivery.pid
        assert resumed_delivery.pid != restarted_delivery.pid
        assert child_result == {"outcome": "verified"}


def test_agent_and_deterministic_workflows_share_runtime_attempt_evidence() -> None:
    with renewal_context(verification_code_secret=b"issue-70-reuse-evidence") as (
        database_url,
        application,
        threads,
    ):
        renewal, _, _, required, _ = _issue_challenge(application, threads)
        assert required.result.verification_instance_id is not None
        renewal_instance_id = application.start_renewal_outreach(renewal).result.instance_id
        with psycopg.connect(database_url) as connection, connection.transaction():
            reader = RuntimeEvidenceReader(connection)
            agent_workflow = reader.instance(renewal_instance_id)
            deterministic_workflow = reader.instance(required.result.verification_instance_id)

        assert agent_workflow.attempts
        assert agent_workflow.agent_runs
        assert deterministic_workflow.attempts
        assert deterministic_workflow.agent_runs == ()
        assert {attempt.state for attempt in deterministic_workflow.attempts} == {"completed"}
        assert (
            KernelInspection(database_url=database_url)
            .snapshot(required.result.verification_instance_id)
            .definition_key
            == "example_insurance.verification_delivery"
        )
