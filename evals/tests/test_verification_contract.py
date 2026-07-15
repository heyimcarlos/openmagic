from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from example_insurance.renewals import (
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityInput,
    RequestProtectedRenewalDetails,
    RequestProtectedRenewalDetailsInput,
    RequestProtectedRenewalDetailsResult,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
    SubmitVerificationCodeResult,
)
from example_insurance.verification_codes import VerificationCodes
from openmagic_evals.harness import (
    VerificationScenario,
    issue_verification_challenge,
    prepare_renewal_approval,
    renewal_context,
)
from openmagic_runtime.commands import Actor, Cause, IdempotencyConflict
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.threads import CreateThread


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
        email = "broker@example.test"
        identifier_thread = threads.create(CreateThread(uuid4(), "email", email))
        provisioned = application.provision_verification_authority(
            ProvisionVerificationAuthority(
                command_id=uuid4(),
                actor=Actor("system", "fixture-authority"),
                cause=Cause("command", str(uuid4())),
                input=ProvisionVerificationAuthorityInput(
                    party_id=party_id,
                    organization_party_id=organization_party_id,
                    workflow_id=renewal.input.workflow_id,
                    email=email,
                    delivery_thread_id=identifier_thread.thread_id,
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
        renewal_thread = threads.read(renewal.input.thread_id)
        email_thread = threads.read(identifier_thread.thread_id)
        code_match = re.search(r"\b(\d{6})\b", email_thread.messages[-1].content)
        assert code_match is not None
        assert challenge_delivery.thread_id == identifier_thread.thread_id
        assert not any(
            re.search(r"\b\d{6}\b", message.content) for message in renewal_thread.messages
        )

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


def test_verification_receipts_and_code_capability_reject_invalid_public_states() -> None:
    secret = b"issue-70-private-code-secret"
    codes = VerificationCodes(secret)

    assert secret.decode() not in repr(codes)
    assert not hasattr(codes, "secret")
    with pytest.raises(ValueError, match="verification-required receipt"):
        RequestProtectedRenewalDetailsResult(
            outcome="verification_required",
            workflow_id=uuid4(),
            challenge_id=None,
            verification_workflow_id=uuid4(),
            verification_instance_id=uuid4(),
            authorized_delivery_id=None,
        )
    with pytest.raises(ValueError, match="authorized receipt"):
        RequestProtectedRenewalDetailsResult(
            outcome="authorized",
            workflow_id=uuid4(),
            challenge_id=uuid4(),
            verification_workflow_id=None,
            verification_instance_id=None,
            authorized_delivery_id=uuid4(),
        )
    with pytest.raises(ValueError, match="verified receipt"):
        SubmitVerificationCodeResult(
            verification_outcome="verified",
            protected_outcome="authorized",
            challenge_id=uuid4(),
            protected_command_id=uuid4(),
            session_id=None,
            authorized_delivery_id=uuid4(),
        )
    invalid_outcome = json.loads('"typo"')
    with pytest.raises(ValueError, match="invalid outcome"):
        RequestProtectedRenewalDetailsResult(
            outcome=invalid_outcome,
            workflow_id=uuid4(),
            challenge_id=None,
            verification_workflow_id=None,
            verification_instance_id=None,
            authorized_delivery_id=None,
        )
    with pytest.raises(ValueError, match="invalid outcome"):
        SubmitVerificationCodeResult(
            verification_outcome=invalid_outcome,
            protected_outcome=None,
            challenge_id=uuid4(),
            protected_command_id=uuid4(),
            session_id=None,
            authorized_delivery_id=None,
        )


@pytest.mark.parametrize(
    "outcome",
    [
        "approval_required",
        "authority_revoked",
        "identifier_revoked",
        "workflow_closed",
        "wrong_party",
        "wrong_purpose",
        "wrong_thread",
    ],
)
def test_terminal_policy_receipts_preserve_every_named_rejection(outcome: str) -> None:
    decoded_outcome = json.loads(json.dumps(outcome))

    receipt = SubmitVerificationCodeResult(
        verification_outcome=decoded_outcome,
        protected_outcome=decoded_outcome,
        challenge_id=uuid4(),
        protected_command_id=uuid4(),
        session_id=None,
        authorized_delivery_id=None,
    )

    assert receipt.verification_outcome == outcome
    assert receipt.protected_outcome == outcome


def test_verification_code_is_single_use_replay_safe_and_serialized() -> None:
    with renewal_context(verification_code_secret=b"issue-70-code-race-secret") as (
        _,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        renewal = scenario.renewal
        actor = scenario.actor
        protected = scenario.protected_command
        required = scenario.challenge_receipt
        code = scenario.code
        assert required.result.challenge_id is not None
        assert code is not None
        assert application.request_protected_renewal_details(protected) == required
        with pytest.raises(IdempotencyConflict):
            application.request_protected_renewal_details(
                replace(protected, input=replace(protected.input, thread_id=uuid4()))
            )
        second = application.request_protected_renewal_details(
            RequestProtectedRenewalDetails(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=protected.input,
            )
        )
        assert second.result.outcome == "verification_required"
        assert second.result.challenge_id != required.result.challenge_id
        assert second.result.verification_instance_id != required.result.verification_instance_id
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


def test_failed_code_budget_is_terminal_and_serializes_final_attempt_races() -> None:
    with renewal_context(verification_code_secret=b"issue-70-code-budget-secret") as (
        _,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        renewal = scenario.renewal
        actor = scenario.actor
        protected = scenario.protected_command
        required = scenario.challenge_receipt
        code = scenario.code
        challenge_id = required.result.challenge_id
        assert challenge_id is not None
        assert code is not None
        wrong_code = "000000" if code != "000000" else "999999"

        def submission(candidate: str) -> SubmitVerificationCode:
            return SubmitVerificationCode(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=challenge_id,
                    protected_command_id=protected.command_id,
                    workflow_id=renewal.input.workflow_id,
                    thread_id=renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code=candidate,
                ),
            )

        for _ in range(4):
            assert (
                application.submit_verification_code(
                    submission(wrong_code)
                ).result.verification_outcome
                == "invalid_code"
            )

        barrier = Barrier(2)

        def submit_final(command: SubmitVerificationCode):
            barrier.wait()
            return application.submit_verification_code(command)

        final_commands = (submission(wrong_code), submission(wrong_code))
        with ThreadPoolExecutor(max_workers=2) as executor:
            final_receipts = tuple(executor.map(submit_final, final_commands))

        later_correct = application.submit_verification_code(submission(code))

        assert [receipt.result.verification_outcome for receipt in final_receipts] == [
            "invalid_code",
            "invalid_code",
        ]
        assert later_correct.result.verification_outcome == "invalid_code"
        assert application.submit_verification_code(final_commands[0]) == final_receipts[0]


def test_pending_challenges_bind_two_exact_protected_workflows_for_one_party_thread() -> None:
    with renewal_context(verification_code_secret=b"issue-70-workflow-binding") as (
        _,
        application,
        threads,
    ):
        first = issue_verification_challenge(application, threads)
        second = issue_verification_challenge(
            application,
            threads,
            actor=first.actor,
            protected_thread_id=first.renewal.input.thread_id,
            identifier_thread_id=first.identifier_thread_id,
            organization_party_id=first.organization_party_id,
        )

        assert first.challenge_receipt.result.challenge_id is not None
        assert second.challenge_receipt.result.challenge_id is not None
        assert first.code is not None
        assert second.code is not None
        assert first.renewal.input.workflow_id != second.renewal.input.workflow_id
        assert (
            first.challenge_receipt.result.challenge_id
            != second.challenge_receipt.result.challenge_id
        )

        def submit(scenario: VerificationScenario):
            challenge_id = scenario.challenge_receipt.result.challenge_id
            if challenge_id is None or scenario.code is None:
                raise AssertionError("Exact Workflow Challenge is incomplete")
            return application.submit_verification_code(
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
                        code=scenario.code,
                    ),
                )
            )

        assert submit(first).result.verification_outcome == "verified"
        assert submit(second).result.verification_outcome == "verified"


def test_challenge_rejects_every_wrong_binding_without_consuming_code() -> None:
    with renewal_context(verification_code_secret=b"issue-70-binding-secret") as (
        _,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        renewal = scenario.renewal
        actor = scenario.actor
        protected = scenario.protected_command
        required = scenario.challenge_receipt
        code = scenario.code
        assert required.result.challenge_id is not None
        assert code is not None
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
        replay_outcomes = tuple(
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
        assert replay_outcomes == outcomes
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
