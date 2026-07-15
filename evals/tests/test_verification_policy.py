from __future__ import annotations

import re
import time
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from example_insurance.renewals import (
    CancelRenewalOutreach,
    CancelRenewalOutreachInput,
    ExampleInsurance,
    ProvisionVerificationAuthority,
    ProvisionVerificationAuthorityInput,
    RequestProtectedRenewalDetails,
    RevokeRenewalAuthority,
    RevokeRenewalAuthorityInput,
    RevokeVerificationAuthority,
    RevokeVerificationAuthorityInput,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
    VerificationAuthorityTarget,
)
from openmagic_evals.harness import (
    issue_verification_challenge,
    prepare_renewal_approval,
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
        submission_input = SubmitVerificationCodeInput(
            challenge_id=required.result.challenge_id,
            protected_command_id=protected.command_id,
            workflow_id=renewal.input.workflow_id,
            thread_id=renewal.input.thread_id,
            purpose="renewal.read_approved_details",
            code=code,
        )
        result = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=submission_input,
            )
        )
        replay = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=actor,
                cause=Cause("message", str(uuid4())),
                input=submission_input,
            )
        )

        assert revoked.result.outcome == "revoked"
        assert result.result.verification_outcome == expected
        assert result.result.protected_outcome == expected
        assert result.result.session_id is None
        assert replay.result.verification_outcome == expected
        assert replay.result.protected_outcome == expected


def test_invalidated_approval_grant_rejects_and_replays_without_collapsing_outcome() -> None:
    with renewal_context(verification_code_secret=b"issue-70-grant-revalidation") as (
        _,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        challenge_id = scenario.challenge_receipt.result.challenge_id
        assert challenge_id is not None
        assert scenario.code is not None
        revoked = application.revoke_renewal_authority(
            RevokeRenewalAuthority(
                command_id=uuid4(),
                actor=Actor("system", "authority-administrator"),
                cause=Cause("command", str(uuid4())),
                input=RevokeRenewalAuthorityInput(
                    workflow_id=scenario.renewal.input.workflow_id,
                    actor_id=scenario.actor.identifier,
                ),
            )
        )

        def submission() -> SubmitVerificationCode:
            return SubmitVerificationCode(
                command_id=uuid4(),
                actor=scenario.actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=challenge_id,
                    protected_command_id=scenario.protected_command.command_id,
                    workflow_id=scenario.renewal.input.workflow_id,
                    thread_id=scenario.renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code=scenario.code or "",
                ),
            )

        rejected = application.submit_verification_code(submission())
        replay = application.submit_verification_code(submission())

        assert revoked.result.outcome == "revoked"
        assert rejected.result.verification_outcome == "approval_required"
        assert rejected.result.protected_outcome == "approval_required"
        assert replay.result.verification_outcome == "approval_required"
        assert replay.result.protected_outcome == "approval_required"


def test_revoked_membership_can_be_reprovisioned_for_the_existing_participant() -> None:
    with renewal_context(verification_code_secret=b"issue-70-membership-reprovision") as (
        _,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        party_id = UUID(scenario.actor.identifier)
        revoked = application.revoke_verification_authority(
            RevokeVerificationAuthority(
                command_id=uuid4(),
                actor=Actor("system", "authority-administrator"),
                cause=Cause("command", str(uuid4())),
                input=RevokeVerificationAuthorityInput(
                    party_id=party_id,
                    workflow_id=scenario.renewal.input.workflow_id,
                    target="membership",
                ),
            )
        )
        identifier = threads.read(scenario.identifier_thread_id)
        provisioned = application.provision_verification_authority(
            ProvisionVerificationAuthority(
                command_id=uuid4(),
                actor=Actor("system", "authority-administrator"),
                cause=Cause("command", str(uuid4())),
                input=ProvisionVerificationAuthorityInput(
                    party_id=party_id,
                    organization_party_id=scenario.organization_party_id,
                    workflow_id=scenario.renewal.input.workflow_id,
                    email=identifier.channel_reference,
                    delivery_thread_id=identifier.thread_id,
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

        assert revoked.result.outcome == "revoked"
        assert provisioned.result.outcome == "provisioned"
        assert replacement.result.outcome == "verification_required"


def test_worker_rejects_a_superseded_identifier_and_rebinds_to_current_email() -> None:
    with renewal_context(verification_code_secret=b"issue-70-current-identifier") as (
        database_url,
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
        verification_instance_id = scenario.challenge_receipt.result.verification_instance_id
        assert verification_instance_id is not None
        assert (
            KernelInspection(database_url=database_url).snapshot(verification_instance_id).state
            == "closed"
        )
        ExampleInsurance(database_url=database_url).prepare_workflow_worker()
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


def test_authority_provisioning_rejects_a_non_party_workflow_actor() -> None:
    with renewal_context(verification_code_secret=b"issue-70-actor-kind") as (
        _,
        application,
        threads,
    ):
        actor = Actor("system", str(uuid4()))
        renewal, _ = prepare_renewal_approval(application, threads, actor=actor)
        email = f"broker-{actor.identifier}@example.test"
        identifier_thread = threads.create(CreateThread(uuid4(), "email", email))

        with pytest.raises(ValueError, match="authorized Actor"):
            application.provision_verification_authority(
                ProvisionVerificationAuthority(
                    command_id=uuid4(),
                    actor=Actor("system", "authority-administrator"),
                    cause=Cause("command", str(uuid4())),
                    input=ProvisionVerificationAuthorityInput(
                        party_id=UUID(actor.identifier),
                        organization_party_id=uuid4(),
                        workflow_id=renewal.input.workflow_id,
                        email=email,
                        delivery_thread_id=identifier_thread.thread_id,
                    ),
                )
            )


def test_authority_provisioning_rejects_an_organization_as_the_person_party() -> None:
    with renewal_context(verification_code_secret=b"issue-70-party-kinds") as (
        _,
        application,
        threads,
    ):
        original = issue_verification_challenge(application, threads)
        organization_actor = Actor("party", str(original.organization_party_id))

        with pytest.raises(ValueError, match="exact person and organization Parties"):
            issue_verification_challenge(
                application,
                threads,
                actor=organization_actor,
                organization_party_id=uuid4(),
            )


def test_delivered_code_is_rejected_after_identifier_replacement() -> None:
    with renewal_context(verification_code_secret=b"issue-70-delivered-supersession") as (
        _,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        old_challenge_id = scenario.challenge_receipt.result.challenge_id
        assert old_challenge_id is not None
        assert scenario.code is not None
        replacement_email = f"replacement-{scenario.actor.identifier}@example.test"
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
        rejected = application.submit_verification_code(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=scenario.actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=old_challenge_id,
                    protected_command_id=scenario.protected_command.command_id,
                    workflow_id=scenario.renewal.input.workflow_id,
                    thread_id=scenario.renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code=scenario.code,
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
        application.run_workflow_worker_once(worker_id="replacement-identifier-worker")
        delivered = application.run_delivery_worker_once(
            worker_id="replacement-identifier-delivery"
        )
        replacement_message = threads.read(replacement_thread.thread_id).messages[-1].content
        replacement_code = re.search(r"\b(\d{6})\b", replacement_message)

        assert rejected.result.verification_outcome == "identifier_revoked"
        assert replacement.result.outcome == "verification_required"
        assert delivered is not None
        assert delivered.thread_id == replacement_thread.thread_id
        assert replacement_code is not None


@pytest.mark.parametrize(
    ("channel_kind", "channel_reference"),
    [("email", "other@example.test"), ("sms", "replacement@example.test")],
)
def test_identifier_provisioning_rejects_mismatched_public_email_thread(
    channel_kind: str,
    channel_reference: str,
) -> None:
    with renewal_context(verification_code_secret=b"issue-70-thread-binding") as (
        _,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        wrong_thread = threads.create(CreateThread(uuid4(), channel_kind, channel_reference))
        with pytest.raises(ValueError, match="exact public email Thread"):
            application.provision_verification_authority(
                ProvisionVerificationAuthority(
                    command_id=uuid4(),
                    actor=Actor("system", "authority-administrator"),
                    cause=Cause("command", str(uuid4())),
                    input=ProvisionVerificationAuthorityInput(
                        party_id=UUID(scenario.actor.identifier),
                        organization_party_id=scenario.organization_party_id,
                        workflow_id=scenario.renewal.input.workflow_id,
                        email="replacement@example.test",
                        delivery_thread_id=wrong_thread.thread_id,
                    ),
                )
            )


def test_identifier_provisioning_rejects_the_protected_thread_as_second_channel() -> None:
    with renewal_context(verification_code_secret=b"issue-70-distinct-thread") as (
        _,
        application,
        threads,
    ):
        scenario = issue_verification_challenge(application, threads)
        protected_thread = threads.read(scenario.renewal.input.thread_id)
        with pytest.raises(ValueError, match="distinct identifier email Thread"):
            application.provision_verification_authority(
                ProvisionVerificationAuthority(
                    command_id=uuid4(),
                    actor=Actor("system", "authority-administrator"),
                    cause=Cause("command", str(uuid4())),
                    input=ProvisionVerificationAuthorityInput(
                        party_id=UUID(scenario.actor.identifier),
                        organization_party_id=scenario.organization_party_id,
                        workflow_id=scenario.renewal.input.workflow_id,
                        email=protected_thread.channel_reference,
                        delivery_thread_id=protected_thread.thread_id,
                    ),
                )
            )


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
        closed_input = SubmitVerificationCodeInput(
            challenge_id=second_required.result.challenge_id,
            protected_command_id=second_protected.command_id,
            workflow_id=second_renewal.input.workflow_id,
            thread_id=second_renewal.input.thread_id,
            purpose="renewal.read_approved_details",
            code=second_code,
        )

        def closed_submission() -> SubmitVerificationCode:
            return SubmitVerificationCode(
                command_id=uuid4(),
                actor=second_actor,
                cause=Cause("message", str(uuid4())),
                input=closed_input,
            )

        closed = application.submit_verification_code(closed_submission())
        closed_replay = application.submit_verification_code(closed_submission())
        snapshot = KernelInspection(database_url=database_url).snapshot(
            cancelled.result.instance_id
        )

        assert cancelled.result.outcome == "cancelled"
        assert closed.result.verification_outcome == "workflow_closed"
        assert closed.result.protected_outcome == "workflow_closed"
        assert closed_replay.result.verification_outcome == "workflow_closed"
        assert closed_replay.result.protected_outcome == "workflow_closed"
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
