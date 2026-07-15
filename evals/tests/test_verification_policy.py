from __future__ import annotations

import time
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from example_insurance.renewals import (
    CancelRenewalOutreach,
    CancelRenewalOutreachInput,
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityInput,
    RequestProtectedRenewalDetails,
    RevokeVerificationAuthority,
    RevokeVerificationAuthorityInput,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
    VerificationAuthorityTarget,
)
from openmagic_evals.harness import (
    issue_verification_challenge,
    renewal_context,
)
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.threads import CreateThread


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
        scenario = issue_verification_challenge(application, threads)
        renewal = scenario.renewal
        actor = scenario.actor
        protected = scenario.protected_command
        required = scenario.challenge_receipt
        code = scenario.code
        assert required.result.challenge_id is not None
        assert code is not None
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


def test_delivery_rejects_a_superseded_identifier_and_rebinds_to_current_email() -> None:
    with renewal_context(verification_code_secret=b"issue-70-current-identifier") as (
        _,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads, run_workflow=False)
        challenge_id = scenario.challenge_receipt.result.challenge_id
        assert challenge_id is not None
        replacement_email = f"current-{scenario.actor.identifier}@example.test"
        replacement_thread = threads.create(CreateThread(uuid4(), "email", replacement_email))
        application.provision_verification_authority(
            ProvisionVerificationAuthority(
                command_id=uuid4(),
                actor=Actor("system", "authority-administrator"),
                cause=Cause("command", str(uuid4())),
                input=ProvisionVerificationAuthorityInput(
                    party_id=UUID(scenario.actor.identifier),
                    organization_party_id=scenario.organization_party_id,
                    workflow_id=scenario.renewal.input.workflow_id,
                    email=replacement_email,
                    delivery_thread_id=replacement_thread.thread_id,
                ),
            )
        )

        application.run_workflow_worker_once(worker_id="superseded-identifier-worker")
        old_result = application.submit_verification_code(
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
        replacement = application.request_protected_renewal_details(
            RequestProtectedRenewalDetails(
                command_id=uuid4(),
                actor=scenario.actor,
                cause=Cause("message", str(uuid4())),
                input=scenario.protected_command.input,
            )
        )
        application.run_workflow_worker_once(worker_id="current-identifier-worker")
        delivered = application.run_delivery_worker_once(worker_id="current-identifier-delivery")

        assert old_result.result.verification_outcome == "identifier_revoked"
        assert replacement.result.outcome == "verification_required"
        assert delivered is not None
        assert delivered.thread_id == replacement_thread.thread_id
        assert threads.read(scenario.identifier_thread_id).messages == ()


def test_expired_challenge_and_closed_workflow_fail_without_mutating_lifecycle() -> None:
    with renewal_context(
        verification_code_secret=b"issue-70-expiry-secret",
        challenge_ttl_seconds=1,
    ) as (database_url, application, threads):
        scenario = issue_verification_challenge(application, threads)
        renewal = scenario.renewal
        actor = scenario.actor
        protected = scenario.protected_command
        required = scenario.challenge_receipt
        code = scenario.code
        assert required.result.challenge_id is not None
        assert code is not None
        time.sleep(1.05)
        replacement = application.request_protected_renewal_details(
            RequestProtectedRenewalDetails(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=protected.input,
            )
        )
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
        assert replacement.result.outcome == "verification_required"
        assert replacement.result.challenge_id != required.result.challenge_id
        assert expired.result.verification_outcome == "expired"
        assert application.submit_verification_code(expired_command) == expired
        application.run_workflow_worker_once(worker_id="replacement-verification")
        application.run_delivery_worker_once(worker_id="replacement-delivery")

        second = issue_verification_challenge(application, threads)
        second_renewal = second.renewal
        second_actor = second.actor
        second_protected = second.protected_command
        second_required = second.challenge_receipt
        second_code = second.code
        assert second_required.result.challenge_id is not None
        assert second_code is not None
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


def test_session_reuses_only_same_party_and_thread_then_expires() -> None:
    with renewal_context(
        verification_code_secret=b"issue-70-session-expiry",
        session_ttl_seconds=1,
    ) as (_, application, threads):
        scenario = issue_verification_challenge(application, threads)
        renewal = scenario.renewal
        actor = scenario.actor
        protected = scenario.protected_command
        required = scenario.challenge_receipt
        code = scenario.code
        assert required.result.challenge_id is not None
        assert code is not None
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
