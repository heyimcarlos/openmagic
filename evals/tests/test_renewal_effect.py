from __future__ import annotations

import json
import os
import pickle
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from example_insurance.migrations import apply_migrations
from example_insurance.renewal_effects import EmailProviderExecutor, ExternalEffectPermit
from example_insurance.renewals import (
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    CancelRenewalOutreach,
    CancelRenewalOutreachInput,
    CancelRenewalOutreachResult,
    ExampleInsurance,
    RenewalFacts,
    RequestRenewalRevision,
    RequestRenewalRevisionInput,
    RevokeRenewalAuthority,
    RevokeRenewalAuthorityInput,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
)
from openmagic_evals.harness import (
    LocalEmailProvider,
    TestDeployment,
    approve_renewal,
    prepare_renewal_approval,
    wait_for_database_fault_window,
    wait_for_renewal_completion,
)
from openmagic_evals.harness._postgres import postgres_container
from openmagic_runtime.commands import Actor, Cause, CommandReceipt, IdempotencyConflict
from openmagic_runtime.execution import AttemptExecution, CancellationToken
from openmagic_runtime.kernel.control import (
    GuardCurrentAttempt,
    KernelControl,
)
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.threads import CreateThread, ThreadStore


@contextmanager
def renewal_context() -> Iterator[tuple[str, ExampleInsurance, ThreadStore]]:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        yield database_url, application, ThreadStore(database_url=database_url)


def test_exact_approval_satisfies_one_wait_and_materializes_the_fenced_email_step() -> None:
    with renewal_context() as (database_url, application, threads):
        thread = threads.create(CreateThread(uuid4(), "email", "broker-approval"))
        actor = Actor(kind="party", identifier=str(uuid4()))
        start = StartRenewalOutreach(
            command_id=uuid4(),
            actor=actor,
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-69",
                policyholder_name="Avery Chen",
                policyholder_email="avery@example.test",
                renewal_date="2027-12-31",
                expiring_premium_cents=250_000,
            ),
        )
        application.replace_renewal_facts(
            RenewalFacts(
                policy_id=start.input.policy_id,
                policy_number=start.input.policy_number,
                policyholder_name=start.input.policyholder_name,
                policyholder_email=start.input.policyholder_email,
                renewal_date=start.input.renewal_date,
                expiring_premium_cents=start.input.expiring_premium_cents,
            )
        )
        started = application.start_renewal_outreach(start)
        application.run_workflow_worker_once(worker_id="facts")
        application.run_workflow_worker_once(worker_id="draft")
        presentation = application.renewal_approval_presentation(start.input.workflow_id)
        command = ApproveRenewalDraft(
            command_id=uuid4(),
            actor=actor,
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=ApproveRenewalDraftInput(
                workflow_id=start.input.workflow_id,
                wait_id=presentation.wait_id,
                draft_id=presentation.draft_id,
                presentation_fingerprint=presentation.presentation_fingerprint,
                proposed_effect=presentation.proposed_effect,
            ),
        )

        receipt = application.approve_renewal_draft(command)
        replay = application.approve_renewal_draft(command)
        snapshot = KernelInspection(database_url=database_url).snapshot(started.result.instance_id)

        assert replay == receipt
        assert receipt.result.outcome == "approved"
        assert receipt.result.wait_id == presentation.wait_id
        assert receipt.result.effect_step_id is not None
        assert [(wait.template_key, wait.state) for wait in snapshot.waits] == [
            ("renewal_draft_approval", "satisfied")
        ]
        assert [(step.template_key, step.state) for step in snapshot.steps][-1] == (
            "send_renewal_email",
            "pending",
        )


def test_successful_provider_evidence_completes_and_closes_the_instance(tmp_path) -> None:
    with LocalEmailProvider(working_directory=tmp_path / "provider") as provider:
        provider.configure(behaviors=("success",))
        with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
            database_url = postgres.get_connection_url(driver=None)
            apply_migrations(database_url)
            application = ExampleInsurance(
                database_url=database_url,
                email_provider_url=provider.url,
            )
            application.prepare()
            threads = ThreadStore(database_url=database_url)
            command, actor = prepare_renewal_approval(application, threads)
            started = application.start_renewal_outreach(command)
            approve_renewal(application, command, actor)

            result = application.run_workflow_worker_once(worker_id="email")
            snapshot = KernelInspection(database_url=database_url).snapshot(
                started.result.instance_id
            )

            assert result is not None
            assert result.template_key == "send_renewal_email"
            assert snapshot.state == "closed"
            assert [(step.template_key, step.state) for step in snapshot.steps][-1] == (
                "send_renewal_email",
                "succeeded",
            )
            requests = provider.requests()
            assert len(requests) == 1
            assert requests[0]["recipient_email"] == "avery@example.test"
            assert requests[0]["duplicate"] == 0


def test_revision_creates_another_bounded_draft_and_exact_approval_wait() -> None:
    with renewal_context() as (database_url, application, threads):
        command, actor = prepare_renewal_approval(application, threads)
        presentation = application.renewal_approval_presentation(command.input.workflow_id)
        revision = RequestRenewalRevision(
            command_id=uuid4(),
            actor=actor,
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=RequestRenewalRevisionInput(
                workflow_id=command.input.workflow_id,
                wait_id=presentation.wait_id,
                draft_id=presentation.draft_id,
                presentation_fingerprint=presentation.presentation_fingerprint,
                proposed_effect=presentation.proposed_effect,
                revision_instruction="Use a warmer opening.",
            ),
        )

        receipt = application.request_renewal_revision(revision)
        replay = application.request_renewal_revision(revision)
        draft_result = application.run_workflow_worker_once(worker_id="revision")
        revised = application.renewal_approval_presentation(command.input.workflow_id)
        snapshot = KernelInspection(database_url=database_url).snapshot(
            application.start_renewal_outreach(command).result.instance_id
        )

        assert replay == receipt
        assert receipt.result.outcome == "revision_requested"
        assert draft_result is not None
        assert draft_result.template_key == "draft_renewal_email"
        assert revised.wait_id != presentation.wait_id
        assert revised.draft_id != presentation.draft_id
        assert "Requested revision: Use a warmer opening." in revised.proposed_effect.body
        assert [step.template_key for step in snapshot.steps].count("draft_renewal_email") == 2
        assert [wait.state for wait in snapshot.waits] == ["satisfied", "unsatisfied"]


def test_definite_non_application_retries_the_same_effect_identity_then_completes(
    tmp_path,
) -> None:
    with LocalEmailProvider(working_directory=tmp_path / "provider") as provider:
        provider.configure(behaviors=("definite_not_applied", "success"))
        with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
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
            effect_step_id = approve_renewal(application, command, actor)

            first = application.run_workflow_worker_once(worker_id="email")
            after_first = KernelInspection(database_url=database_url).snapshot(
                started.result.instance_id
            )
            second = application.run_workflow_worker_once(worker_id="email")
            completed = KernelInspection(database_url=database_url).snapshot(
                started.result.instance_id
            )

            assert first is not None
            assert first.template_key == "send_renewal_email"
            assert second is not None
            assert second.template_key == "send_renewal_email"
            assert after_first.state == "open"
            assert next(
                step for step in after_first.steps if step.step_id == effect_step_id
            ).state == ("pending")
            assert completed.state == "closed"
            requests = provider.requests()
            assert len(requests) == 2
            assert requests[0]["idempotency_key"] == requests[1]["idempotency_key"]
            assert [request["behavior"] for request in requests] == [
                "definite_not_applied",
                "success",
            ]


def test_definite_non_application_exhaustion_fails_without_uncertain_reconciliation(
    tmp_path,
) -> None:
    with LocalEmailProvider(working_directory=tmp_path / "provider") as provider:
        provider.configure(
            behaviors=(
                "definite_not_applied",
                "definite_not_applied",
                "definite_not_applied",
            )
        )
        with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
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

            for _ in range(3):
                result = application.run_workflow_worker_once(worker_id="email")
                assert result is not None
            snapshot = KernelInspection(database_url=database_url).snapshot(
                started.result.instance_id
            )

            assert snapshot.state == "open"
            assert snapshot.steps[-1].template_key == "send_renewal_email"
            assert snapshot.steps[-1].state == "failed"
            assert all(step.template_key != "reconcile_renewal_email" for step in snapshot.steps)
            assert len(provider.requests()) == 3


def test_reconciliation_cannot_reopen_an_exhausted_effect_attempt_budget(tmp_path) -> None:
    with LocalEmailProvider(working_directory=tmp_path / "provider") as provider:
        provider.configure(
            behaviors=("definite_not_applied", "definite_not_applied", "uncertain"),
            reconciliation="not_applied",
        )
        with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
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
            effect_step_id = approve_renewal(application, command, actor)

            for _ in range(3):
                assert application.run_workflow_worker_once(worker_id="email") is not None
            assert application.run_workflow_worker_once(worker_id="reconciler") is not None
            snapshot = KernelInspection(database_url=database_url).snapshot(
                started.result.instance_id
            )
            effect_step = next(step for step in snapshot.steps if step.step_id == effect_step_id)

            assert effect_step.state == "failed"
            assert snapshot.steps[-1].template_key == "reconcile_renewal_email"
            assert snapshot.steps[-1].state == "succeeded"
            assert len(provider.requests()) == 3


def test_response_loss_defers_email_retry_until_fresh_provider_reconciliation(
    tmp_path,
) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path / "provider")
    provider.start()
    try:
        provider.configure(behaviors=("response_loss_after_success",))
        with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
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

            dispatched = application.run_workflow_worker_once(worker_id="email")
            uncertain = KernelInspection(database_url=database_url).snapshot(
                started.result.instance_id
            )
            requests_before_reconciliation = provider.requests()
            previous_provider_pid = provider.pid
            provider.restart()
            reconciled = application.run_workflow_worker_once(worker_id="reconciler")
            completed = KernelInspection(database_url=database_url).snapshot(
                started.result.instance_id
            )

            assert dispatched is not None
            assert dispatched.template_key == "send_renewal_email"
            assert uncertain.state == "open"
            assert [step.template_key for step in uncertain.steps][-1] == (
                "reconcile_renewal_email"
            )
            assert len(requests_before_reconciliation) == 1
            assert provider.pid != previous_provider_pid
            assert reconciled is not None
            assert reconciled.template_key == "reconcile_renewal_email"
            assert completed.state == "closed"
            assert len(provider.requests()) == 1
    finally:
        provider.stop()


def test_provider_reuses_one_result_for_duplicate_idempotency_identity(tmp_path) -> None:
    with LocalEmailProvider(working_directory=tmp_path / "provider") as provider:
        provider.configure(behaviors=("success",))
        step_id = uuid4()
        executor = EmailProviderExecutor(provider_url=provider.url)
        execution = AttemptExecution(
            instance_id=uuid4(),
            step_id=step_id,
            attempt_id=uuid4(),
            attempt_number=1,
            template_key="send_renewal_email",
            executor_key="example_insurance.email_provider.v1",
            input={
                "recipient_email": "duplicate@example.test",
                "subject": "Duplicate identity",
                "body": "One logical email",
            },
        )

        first = executor.execute(execution, CancellationToken())
        duplicate = executor.execute(
            replace(execution, attempt_id=uuid4(), attempt_number=2),
            CancellationToken(),
        )

        assert duplicate == first
        requests = provider.requests()
        assert len(requests) == 2
        assert requests[0]["idempotency_key"] == requests[1]["idempotency_key"]
        assert [request["duplicate"] for request in requests] == [0, 1]


def test_explicit_uncertainty_never_retries_email_before_reconciliation(tmp_path) -> None:
    with (
        LocalEmailProvider(working_directory=tmp_path / "provider") as provider,
        postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres,
    ):
        provider.configure(behaviors=("uncertain",), reconciliation="applied")
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

        application.run_workflow_worker_once(worker_id="email")
        uncertain = KernelInspection(database_url=database_url).snapshot(started.result.instance_id)
        requests_before_reconciliation = provider.requests()
        application.run_workflow_worker_once(worker_id="reconciler")
        completed = KernelInspection(database_url=database_url).snapshot(started.result.instance_id)

        assert uncertain.state == "open"
        assert [step.template_key for step in uncertain.steps][-1] == ("reconcile_renewal_email")
        assert len(requests_before_reconciliation) == 1
        assert len(provider.requests()) == 1
        assert completed.state == "closed"


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


def test_competing_approval_and_revision_signals_have_one_nontransferable_winner() -> None:
    with renewal_context() as (_, application, threads):
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
                presentation_fingerprint=presentation.presentation_fingerprint,
                proposed_effect=presentation.proposed_effect,
                revision_instruction="Prefer a shorter explanation.",
            ),
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            approval_future = executor.submit(application.approve_renewal_draft, approval)
            revision_future = executor.submit(application.request_renewal_revision, revision)
            results = (approval_future.result(), revision_future.result())

        outcomes = {receipt.result.outcome for receipt in results}
        assert "wait_already_satisfied" in outcomes
        assert len(outcomes & {"approved", "revision_requested"}) == 1
        winners = [
            receipt
            for receipt in results
            if receipt.result.outcome in {"approved", "revision_requested"}
        ]
        assert len(winners) == 1


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

        assert permit.step_id == claimed.step_id
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
        barrier = Barrier(2)

        def fence() -> ExternalEffectPermit | RuntimeError:
            barrier.wait()
            try:
                return application.authorize_email_dispatch(attempt=claimed, worker_id="email")
            except RuntimeError as error:
                return error

        def cancel() -> CommandReceipt[CancelRenewalOutreachResult]:
            barrier.wait()
            return application.cancel_renewal_outreach(
                CancelRenewalOutreach(
                    command_id=uuid4(),
                    actor=actor,
                    cause=Cause(kind="message", identifier=str(uuid4())),
                    input=CancelRenewalOutreachInput(command.input.workflow_id),
                )
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            fence_future = executor.submit(fence)
            cancel_future = executor.submit(cancel)
            fence_result = fence_future.result()
            cancel_result = cancel_future.result()

        if isinstance(fence_result, RuntimeError):
            assert cancel_result.result.outcome == "cancelled"
        else:
            assert fence_result.step_id == claimed.step_id
            assert cancel_result.result.outcome == "too_late"


def test_current_attempt_guard_rejects_expired_abandoned_and_superseded_authority(
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
        approve_renewal(application, command, actor)
        original = application.claim_workflow_attempt(
            worker_id="email",
            claim_request_id=uuid4(),
        )
        assert original is not None
        request = GuardCurrentAttempt(
            instance_id=original.instance_id,
            step_id=original.step_id,
            attempt_id=original.attempt_id,
            attempt_number=original.attempt_number,
        )
        with psycopg.connect(database_url) as connection, connection.transaction():
            guard = KernelControl(connection).guard_current_attempt(request)
            guard.require_usable()
            with pytest.raises(TypeError, match="cannot be serialized"):
                pickle.dumps(guard)
        with pytest.raises(RuntimeError, match="no longer transaction-scoped"):
            guard.require_usable()

        time.sleep(1.1)
        assert application.recover_expired_workflow_attempt()
        replacement = application.claim_workflow_attempt(
            worker_id="replacement",
            claim_request_id=uuid4(),
        )
        assert replacement is not None
        assert replacement.attempt_number == original.attempt_number + 1
        with psycopg.connect(database_url) as connection, connection.transaction():
            control = KernelControl(connection)
            with pytest.raises(RuntimeError, match="not current"):
                control.guard_current_attempt(request)
            control.guard_current_attempt(
                GuardCurrentAttempt(
                    instance_id=replacement.instance_id,
                    step_id=replacement.step_id,
                    attempt_id=replacement.attempt_id,
                    attempt_number=replacement.attempt_number,
                )
            ).require_usable()


@pytest.mark.integration
def test_fresh_process_recovers_after_fence_commit_before_provider_io(tmp_path) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path / "provider")
    deployment = TestDeployment(
        working_directory=tmp_path / "deployment",
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(behaviors=("success",))
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=deployment.database_url),
        )
        approve_renewal(application, command, actor)

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "openmagic_evals.harness.fence_once",
                "--database-url",
                deployment.database_url,
                "--email-provider-url",
                provider.url,
                "--worker-id",
                "fence-only-process",
            ],
            cwd=tmp_path,
            env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1"},
            capture_output=True,
            check=True,
        )
        fenced = json.loads(completed.stdout)

        assert UUID(fenced["attempt_id"])
        assert provider.requests() == ()
        time.sleep(1.1)
        recovery_process = deployment.restart_role("workflow-worker")
        evidence = wait_for_renewal_completion(application, command.input.workflow_id)

        assert recovery_process.pid > 0
        assert evidence["outcomes"]["external_effect_certainties"] == ["applied"]
        assert len(provider.reconciliations()) >= 1
        assert len(provider.requests()) == 1


@pytest.mark.integration
def test_fresh_process_loss_before_fence_allows_only_safe_retry(tmp_path) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path / "provider")
    deployment = TestDeployment(
        working_directory=tmp_path / "deployment",
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(behaviors=("success",))
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=deployment.database_url),
        )
        approve_renewal(application, command, actor)
        with psycopg.connect(deployment.database_url) as connection, connection.transaction():
            connection.execute(
                "CREATE FUNCTION example_insurance.pause_effect_fence() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(10); RETURN NEW; END $$"
            )
            connection.execute(
                "CREATE TRIGGER pause_effect_fence BEFORE INSERT ON "
                "example_insurance.external_effects FOR EACH ROW EXECUTE FUNCTION "
                "example_insurance.pause_effect_fence()"
            )

        lost = deployment.restart_role("workflow-worker")
        wait_for_database_fault_window(
            deployment.database_url,
            "INSERT INTO example_insurance.external_effects",
        )
        deployment.terminate_role("workflow-worker")
        with psycopg.connect(deployment.database_url) as connection, connection.transaction():
            connection.execute(
                "DROP TRIGGER pause_effect_fence ON example_insurance.external_effects"
            )
            connection.execute("DROP FUNCTION example_insurance.pause_effect_fence()")
        time.sleep(1.1)
        recovered = deployment.restart_role("workflow-worker")
        evidence = wait_for_renewal_completion(application, command.input.workflow_id)

        assert recovered.pid != lost.pid
        assert evidence["outcomes"]["attempt_states"].count("abandoned") == 1
        assert len(provider.requests()) == 1


@pytest.mark.integration
def test_fresh_process_loss_during_reconciliation_preserves_uncertainty(tmp_path) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path / "provider")
    deployment = TestDeployment(
        working_directory=tmp_path / "deployment",
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(
            behaviors=("response_loss_after_success",),
            reconciliation="slow_applied",
            delay_seconds=3,
        )
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=deployment.database_url),
        )
        approve_renewal(application, command, actor)

        lost = deployment.restart_role("workflow-worker")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not provider.reconciliations():
            time.sleep(0.02)
        assert provider.reconciliations()
        deployment.terminate_role("workflow-worker")
        time.sleep(3.2)
        provider.configure(behaviors=("success",), reconciliation="unchanged")
        recovered = deployment.restart_role("workflow-worker")
        evidence = wait_for_renewal_completion(application, command.input.workflow_id)

        assert recovered.pid != lost.pid
        assert len(provider.requests()) == 1
        assert evidence["outcomes"]["completion_event_count"] == 1
        assert any(
            item["classification"] == "uncertain"
            for item in evidence["outcomes"]["effect_evidence"]
        )


@pytest.mark.integration
def test_fresh_process_loss_during_provider_io_reconciles_without_redispatch(tmp_path) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path / "provider")
    deployment = TestDeployment(
        working_directory=tmp_path / "deployment",
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(
            behaviors=("slow_success",),
            reconciliation="unchanged",
            delay_seconds=3,
        )
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=deployment.database_url),
        )
        approve_renewal(application, command, actor)

        lost = deployment.restart_role("workflow-worker")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not provider.requests():
            time.sleep(0.02)
        assert provider.requests()
        deployment.terminate_role("workflow-worker")
        time.sleep(3.2)
        recovered = deployment.restart_role("workflow-worker")
        evidence = wait_for_renewal_completion(application, command.input.workflow_id)

        assert recovered.pid != lost.pid
        assert len(provider.requests()) == 1
        assert len(provider.reconciliations()) >= 1
        assert evidence["outcomes"]["external_effect_certainties"] == ["applied"]


@pytest.mark.integration
def test_completion_event_and_instance_closure_recover_atomically(tmp_path) -> None:
    provider = LocalEmailProvider(working_directory=tmp_path / "provider")
    deployment = TestDeployment(
        working_directory=tmp_path / "deployment",
        email_provider_url=provider.url,
    )
    with provider, deployment:
        provider.configure(behaviors=("success",))
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(
            database_url=deployment.database_url,
            email_provider_url=provider.url,
        )
        application.prepare()
        command, actor = prepare_renewal_approval(
            application,
            ThreadStore(database_url=deployment.database_url),
        )
        approve_renewal(application, command, actor)
        with psycopg.connect(deployment.database_url) as connection, connection.transaction():
            connection.execute(
                "CREATE FUNCTION example_insurance.pause_completion() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN IF NEW.lifecycle = 'completed' THEN "
                "PERFORM pg_sleep(10); END IF; RETURN NEW; END $$"
            )
            connection.execute(
                "CREATE TRIGGER pause_completion BEFORE UPDATE ON "
                "example_insurance.renewal_workflows FOR EACH ROW EXECUTE FUNCTION "
                "example_insurance.pause_completion()"
            )

        lost = deployment.restart_role("workflow-worker")
        wait_for_database_fault_window(
            deployment.database_url,
            "UPDATE example_insurance.renewal_workflows SET lifecycle = 'completed'",
        )
        deployment.terminate_role("workflow-worker")
        with psycopg.connect(deployment.database_url) as connection, connection.transaction():
            connection.execute(
                "DROP TRIGGER pause_completion ON example_insurance.renewal_workflows"
            )
            connection.execute("DROP FUNCTION example_insurance.pause_completion()")
        time.sleep(1.1)
        recovered = deployment.restart_role("workflow-worker")
        evidence = wait_for_renewal_completion(application, command.input.workflow_id)
        deployment.terminate_role("workflow-worker")
        after_commit = deployment.restart_role("workflow-worker")
        replayed_evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))

        assert recovered.pid != lost.pid
        assert after_commit.pid != recovered.pid
        assert evidence == replayed_evidence
        assert evidence["outcomes"]["workflow_lifecycle"] == "completed"
        assert evidence["outcomes"]["instance_state"] == "closed"
        assert evidence["outcomes"]["completion_event_count"] == 1
        assert len(provider.requests()) == 1
