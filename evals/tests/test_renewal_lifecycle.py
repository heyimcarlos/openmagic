from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from example_insurance.migrations import apply_migrations
from example_insurance.renewal_effect_types import ExternalEffectPermit
from example_insurance.renewals import (
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    ApproveRenewalDraftResult,
    CancelRenewalOutreach,
    CancelRenewalOutreachInput,
    CancelRenewalOutreachResult,
    ExampleInsurance,
    RequestRenewalRevision,
    RequestRenewalRevisionInput,
    RequestRenewalRevisionResult,
    RevokeRenewalAuthority,
    RevokeRenewalAuthorityInput,
)
from openmagic_evals.harness import (
    LocalEmailProvider,
    approve_renewal,
    prepare_renewal_approval,
    renewal_context,
)
from openmagic_evals.harness._postgres import postgres_container
from openmagic_runtime.commands import Actor, Cause, CommandReceipt, IdempotencyConflict
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.kernel.work import ClaimedAttempt
from openmagic_runtime.threads import ThreadStore


def wait_for_race(barrier: Barrier, delay: float) -> None:
    barrier.wait()
    time.sleep(delay)


def race_approval(
    barrier: Barrier,
    delay: float,
    application: ExampleInsurance,
    command: ApproveRenewalDraft,
) -> CommandReceipt[ApproveRenewalDraftResult]:
    wait_for_race(barrier, delay)
    return application.approve_renewal_draft(command)


def race_revision(
    barrier: Barrier,
    delay: float,
    application: ExampleInsurance,
    command: RequestRenewalRevision,
) -> CommandReceipt[RequestRenewalRevisionResult]:
    wait_for_race(barrier, delay)
    return application.request_renewal_revision(command)


def race_cancellation(
    barrier: Barrier,
    delay: float,
    application: ExampleInsurance,
    command: CancelRenewalOutreach,
) -> CommandReceipt[CancelRenewalOutreachResult]:
    wait_for_race(barrier, delay)
    return application.cancel_renewal_outreach(command)


def authorize_or_error(
    barrier: Barrier,
    delay: float,
    application: ExampleInsurance,
    attempt: ClaimedAttempt,
) -> CommandReceipt[ExternalEffectPermit] | RuntimeError:
    wait_for_race(barrier, delay)
    try:
        return application.authorize_email_dispatch(attempt=attempt, worker_id="email")
    except RuntimeError as error:
        return error


def test_stale_presentation_and_revoked_authority_return_replayable_typed_outcomes() -> None:
    with renewal_context() as (_, application, threads):
        command, actor = prepare_renewal_approval(application, threads)
        presentation = application.renewal_approval_presentation(command.input.workflow_id)
        stale_command = ApproveRenewalDraft(
            command_id=uuid4(),
            actor=actor,
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=ApproveRenewalDraftInput(
                workflow_id=command.input.workflow_id,
                wait_id=presentation.wait_id,
                draft_id=presentation.draft_id,
                message_id=presentation.message_id,
                thread_sequence=presentation.thread_sequence,
                message_fingerprint=presentation.message_fingerprint,
                presentation_fingerprint=presentation.presentation_fingerprint,
                proposed_effect=replace(
                    presentation.proposed_effect,
                    subject="A changed and unpresented subject",
                ),
            ),
        )

        stale = application.approve_renewal_draft(stale_command)
        stale_replay = application.approve_renewal_draft(stale_command)
        unauthorized = application.approve_renewal_draft(
            replace(stale_command, command_id=uuid4(), actor=Actor("party", str(uuid4())))
        )
        with pytest.raises(IdempotencyConflict):
            application.approve_renewal_draft(
                replace(
                    stale_command,
                    input=replace(
                        stale_command.input,
                        presentation_fingerprint="conflicting-command-content",
                    ),
                )
            )
        revocation = application.revoke_renewal_authority(
            RevokeRenewalAuthority(
                command_id=uuid4(),
                actor=Actor(kind="system", identifier="authority-administrator"),
                cause=Cause(kind="command", identifier=str(uuid4())),
                input=RevokeRenewalAuthorityInput(
                    workflow_id=command.input.workflow_id,
                    actor_id=actor.identifier,
                ),
            )
        )
        after_revocation = application.approve_renewal_draft(
            ApproveRenewalDraft(
                command_id=uuid4(),
                actor=actor,
                cause=Cause(kind="message", identifier=str(uuid4())),
                input=ApproveRenewalDraftInput(
                    workflow_id=command.input.workflow_id,
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

        assert stale_replay == stale
        assert stale.result.outcome == "stale_presentation"
        assert unauthorized.result.outcome == "unauthorized_actor"
        assert stale.result.approval_grant_id is None
        assert revocation.result.outcome == "revoked"
        assert after_revocation.result.outcome == "authority_revoked"
        assert after_revocation.result.approval_grant_id is None
        assert (
            application.renewal_approval_presentation(command.input.workflow_id).wait_id
            == presentation.wait_id
        )


def test_revocation_before_fence_invalidates_the_exact_approval_grant(tmp_path) -> None:
    with (
        LocalEmailProvider(working_directory=tmp_path / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(
            database_url=database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=database_url),
        )
        approve_renewal(application, command, actor)
        claimed = application.claim_workflow_attempt(
            worker_id="email",
            claim_request_id=uuid4(),
        )
        assert claimed is not None

        application.revoke_renewal_authority(
            RevokeRenewalAuthority(
                command_id=uuid4(),
                actor=Actor(kind="system", identifier="authority-administrator"),
                cause=Cause(kind="command", identifier=str(uuid4())),
                input=RevokeRenewalAuthorityInput(
                    workflow_id=command.input.workflow_id,
                    actor_id=actor.identifier,
                ),
            )
        )

        with pytest.raises(RuntimeError, match="no longer authorizes"):
            application.authorize_email_dispatch(attempt=claimed, worker_id="email")
        assert provider.requests() == ()


def test_revocation_after_fence_preserves_committed_dispatch_authority(tmp_path) -> None:
    with (
        LocalEmailProvider(working_directory=tmp_path / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        provider.configure(behaviors=("success",))
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(
            database_url=database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=database_url),
        )
        started = application.start_renewal_outreach(command)
        approve_renewal(application, command, actor)
        claimed = application.claim_workflow_attempt(
            worker_id="email",
            claim_request_id=uuid4(),
        )
        assert claimed is not None
        permit = application.authorize_email_dispatch(attempt=claimed, worker_id="email")

        application.revoke_renewal_authority(
            RevokeRenewalAuthority(
                command_id=uuid4(),
                actor=Actor(kind="system", identifier="authority-administrator"),
                cause=Cause(kind="command", identifier=str(uuid4())),
                input=RevokeRenewalAuthorityInput(
                    workflow_id=command.input.workflow_id,
                    actor_id=actor.identifier,
                ),
            )
        )
        result = application.complete_workflow_attempt(attempt=claimed, worker_id="email")
        snapshot = KernelInspection(database_url=database_url).snapshot(started.result.instance_id)

        assert permit.result.step_id == claimed.step_id
        assert result.template_key == "send_renewal_email"
        assert snapshot.state == "closed"
        assert len(provider.requests()) == 1


def test_competing_approval_and_revision_signals_have_one_nontransferable_winner() -> None:
    with renewal_context() as (_, application, threads):
        for seed in range(100):
            command, actor = prepare_renewal_approval(application, threads)
            presentation = application.renewal_approval_presentation(command.input.workflow_id)
            approval = ApproveRenewalDraft(
                command_id=uuid4(),
                actor=actor,
                cause=Cause(kind="message", identifier=str(uuid4())),
                input=ApproveRenewalDraftInput(
                    workflow_id=command.input.workflow_id,
                    wait_id=presentation.wait_id,
                    draft_id=presentation.draft_id,
                    message_id=presentation.message_id,
                    thread_sequence=presentation.thread_sequence,
                    message_fingerprint=presentation.message_fingerprint,
                    presentation_fingerprint=presentation.presentation_fingerprint,
                    proposed_effect=presentation.proposed_effect,
                ),
            )
            revision = RequestRenewalRevision(
                command_id=uuid4(),
                actor=actor,
                cause=Cause(kind="message", identifier=str(uuid4())),
                input=RequestRenewalRevisionInput(
                    workflow_id=command.input.workflow_id,
                    wait_id=presentation.wait_id,
                    draft_id=presentation.draft_id,
                    message_id=presentation.message_id,
                    thread_sequence=presentation.thread_sequence,
                    message_fingerprint=presentation.message_fingerprint,
                    presentation_fingerprint=presentation.presentation_fingerprint,
                    proposed_effect=presentation.proposed_effect,
                    revision_instruction="Prefer a shorter explanation.",
                ),
            )
            barrier = Barrier(2)
            jitter = random.Random(seed)

            with ThreadPoolExecutor(max_workers=2) as executor:
                approval_future = executor.submit(
                    race_approval,
                    barrier,
                    jitter.random() / 1000,
                    application,
                    approval,
                )
                revision_future = executor.submit(
                    race_revision,
                    barrier,
                    jitter.random() / 1000,
                    application,
                    revision,
                )
                approval_result = approval_future.result()
                revision_result = revision_future.result()
                results = (approval_result, revision_result)

            outcomes = {receipt.result.outcome for receipt in results}
            assert "wait_already_satisfied" in outcomes, seed
            assert len(outcomes & {"approved", "revision_requested"}) == 1, seed
            cleanup = application.cancel_renewal_outreach(
                CancelRenewalOutreach(
                    command_id=uuid4(),
                    actor=actor,
                    cause=Cause(kind="command", identifier=str(uuid4())),
                    input=CancelRenewalOutreachInput(command.input.workflow_id),
                )
            )
            assert cleanup.result.outcome == "cancelled", seed


def test_cancellation_before_fence_closes_work_and_invalidates_dispatch_authority(
    tmp_path,
) -> None:
    with (
        LocalEmailProvider(working_directory=tmp_path / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(
            database_url=database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=database_url),
        )
        started = application.start_renewal_outreach(command)
        approve_renewal(application, command, actor)
        claimed = application.claim_workflow_attempt(
            worker_id="email",
            claim_request_id=uuid4(),
        )
        assert claimed is not None

        cancelled = application.cancel_renewal_outreach(
            CancelRenewalOutreach(
                command_id=uuid4(),
                actor=actor,
                cause=Cause(kind="message", identifier=str(uuid4())),
                input=CancelRenewalOutreachInput(command.input.workflow_id),
            )
        )

        assert cancelled.result.outcome == "cancelled"
        assert (
            KernelInspection(database_url=database_url).snapshot(started.result.instance_id).state
            == "closed"
        )
        with pytest.raises(RuntimeError, match=r"no longer authorizes|closed|current|stale"):
            application.authorize_email_dispatch(attempt=claimed, worker_id="email")
        assert provider.requests() == ()


def test_fence_before_cancellation_makes_cancellation_too_late(tmp_path) -> None:
    with (
        LocalEmailProvider(working_directory=tmp_path / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(
            database_url=database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=database_url),
        )
        started = application.start_renewal_outreach(command)
        approve_renewal(application, command, actor)
        claimed = application.claim_workflow_attempt(
            worker_id="email",
            claim_request_id=uuid4(),
        )
        assert claimed is not None
        permit = application.authorize_email_dispatch(attempt=claimed, worker_id="email")

        cancelled = application.cancel_renewal_outreach(
            CancelRenewalOutreach(
                command_id=uuid4(),
                actor=actor,
                cause=Cause(kind="message", identifier=str(uuid4())),
                input=CancelRenewalOutreachInput(command.input.workflow_id),
            )
        )

        assert permit.result.step_id == claimed.step_id
        assert cancelled.result.outcome == "too_late"
        assert (
            KernelInspection(database_url=database_url).snapshot(started.result.instance_id).state
            == "open"
        )


def test_concurrent_cancellation_and_fence_serialize_to_one_valid_authority_order(
    tmp_path,
) -> None:
    with (
        LocalEmailProvider(working_directory=tmp_path / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(
            database_url=database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        threads = ThreadStore(database_url=database_url)
        for seed in range(100):
            command, actor = prepare_renewal_approval(application, threads)
            approve_renewal(application, command, actor)
            claimed = application.claim_workflow_attempt(
                worker_id="email",
                claim_request_id=uuid4(),
            )
            assert claimed is not None
            barrier = Barrier(2)
            jitter = random.Random(seed)
            cancellation = CancelRenewalOutreach(
                command_id=uuid4(),
                actor=actor,
                cause=Cause(kind="message", identifier=str(uuid4())),
                input=CancelRenewalOutreachInput(command.input.workflow_id),
            )

            with ThreadPoolExecutor(max_workers=2) as executor:
                fence_future = executor.submit(
                    authorize_or_error,
                    barrier,
                    jitter.random() / 1000,
                    application,
                    claimed,
                )
                cancel_future = executor.submit(
                    race_cancellation,
                    barrier,
                    jitter.random() / 1000,
                    application,
                    cancellation,
                )
                fence_result = fence_future.result()
                cancel_result = cancel_future.result()

            if isinstance(fence_result, RuntimeError):
                assert cancel_result.result.outcome == "cancelled", seed
            else:
                assert fence_result.result.step_id == claimed.step_id, seed
                assert cancel_result.result.outcome == "too_late", seed
                completed = application.complete_workflow_attempt(
                    attempt=claimed,
                    worker_id="email",
                )
                assert completed.template_key == "send_renewal_email", seed
