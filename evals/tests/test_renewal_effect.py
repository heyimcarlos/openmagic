from __future__ import annotations

import json
import random
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
from example_insurance.renewal_commands import (
    AcceptRenewalEffectObservation,
    AcceptRenewalEffectObservationInput,
    RenewalEffectObservation,
    effect_observation_command_id,
)
from example_insurance.renewal_effects import EmailProviderExecutor, ExternalEffectPermit
from example_insurance.renewals import (
    ApproveRenewalDraft,
    ApproveRenewalDraftInput,
    ApproveRenewalDraftResult,
    CancelRenewalOutreach,
    CancelRenewalOutreachInput,
    CancelRenewalOutreachResult,
    ExampleInsurance,
    RenewalFacts,
    RequestRenewalRevision,
    RequestRenewalRevisionInput,
    RequestRenewalRevisionResult,
    RevokeRenewalAuthority,
    RevokeRenewalAuthorityInput,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
)
from openmagic_evals.harness import (
    LocalEmailProvider,
    approve_renewal,
    prepare_renewal_approval,
)
from openmagic_evals.harness._postgres import postgres_container
from openmagic_runtime.commands import Actor, Cause, CommandReceipt, IdempotencyConflict
from openmagic_runtime.execution import AttemptExecution, CancellationToken
from openmagic_runtime.kernel.control import KernelControl
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.kernel.transitions import ResolveDeferredStep
from openmagic_runtime.kernel.work import ClaimedAttempt
from openmagic_runtime.threads import CreateThread, ThreadStore


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
            evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))
            assert len(requests) == 1
            assert requests[0]["recipient_email"] == "avery@example.test"
            assert requests[0]["duplicate"] == 0
            decision = evidence["outcomes"]["decisions"][0]
            grant = evidence["outcomes"]["approval_grants"][0]
            effect = evidence["outcomes"]["external_effects"][0]
            applied_evidence = evidence["outcomes"]["effect_evidence"][-1]
            assert decision["signal_id"] in evidence["correlations"]["signal_ids"]
            assert grant["decision_id"] == decision["decision_id"]
            assert effect["approval_grant_id"] == grant["approval_grant_id"]
            assert applied_evidence["logical_effect_id"] == effect["logical_effect_id"]


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
        evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))

        assert replay == receipt
        assert receipt.result.outcome == "revision_requested"
        assert draft_result is not None
        assert draft_result.template_key == "draft_renewal_email"
        assert revised.wait_id != presentation.wait_id
        assert revised.draft_id != presentation.draft_id
        assert "Requested revision: Use a warmer opening." in revised.proposed_effect.body
        assert [step.template_key for step in snapshot.steps].count("draft_renewal_email") == 2
        assert [wait.state for wait in snapshot.waits] == ["satisfied", "unsatisfied"]
        step_states = evidence["outcomes"]["step_states"]
        assert set(step_states) == set(evidence["correlations"]["step_ids"])
        assert [item["template_key"] for item in step_states.values()].count(
            "draft_renewal_email"
        ) == 2

        cross_wired = application.approve_renewal_draft(
            ApproveRenewalDraft(
                command_id=uuid4(),
                actor=actor,
                cause=Cause(kind="message", identifier=str(uuid4())),
                input=ApproveRenewalDraftInput(
                    workflow_id=command.input.workflow_id,
                    wait_id=revised.wait_id,
                    draft_id=presentation.draft_id,
                    presentation_fingerprint=presentation.presentation_fingerprint,
                    proposed_effect=presentation.proposed_effect,
                ),
            )
        )
        assert cross_wired.result.outcome == "stale_presentation"


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
            assert (
                next(step for step in after_first.steps if step.step_id == effect_step_id).state
                == "pending"
            )
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
            with psycopg.connect(database_url) as connection:
                basis = connection.execute(
                    "SELECT attempt_id FROM openmagic_runtime.attempts WHERE step_id = %s "
                    "ORDER BY attempt_number DESC LIMIT 1",
                    (effect_step_id,),
                ).fetchone()
                source = connection.execute(
                    "SELECT a.attempt_id FROM openmagic_runtime.attempts a "
                    "JOIN openmagic_runtime.steps s ON s.step_id = a.step_id "
                    "WHERE s.instance_id = %s AND s.template_key = 'reconcile_renewal_email' "
                    "ORDER BY a.attempt_number DESC LIMIT 1",
                    (started.result.instance_id,),
                ).fetchone()
                assert basis is not None
                assert source is not None
                resolution = ResolveDeferredStep(
                    source_id=UUID(str(source[0])),
                    instance_id=started.result.instance_id,
                    step_id=effect_step_id,
                    basis_attempt_id=UUID(str(basis[0])),
                    action="fail",
                    failure={"class": "external_effect_attempt_budget_exhausted"},
                )
                with connection.transaction():
                    first_replay = KernelControl(connection).resolve_deferred(resolution)
                    second_replay = KernelControl(connection).resolve_deferred(resolution)
                    with pytest.raises(ValueError, match="conflicting input"):
                        KernelControl(connection).resolve_deferred(
                            replace(
                                resolution,
                                action="retry",
                                failure=None,
                            )
                        )

            assert effect_step.state == "failed"
            assert second_replay == first_replay
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


def test_dispatch_and_provider_evidence_commands_return_exact_replay_receipts(tmp_path) -> None:
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
        approve_renewal(application, command, actor)
        attempt = application.claim_workflow_attempt(
            worker_id="email",
            claim_request_id=uuid4(),
        )
        assert attempt is not None

        permit = application.authorize_email_dispatch(attempt=attempt, worker_id="email")
        permit_replay = application.authorize_email_dispatch(
            attempt=attempt,
            worker_id="email",
        )
        observation = EmailProviderExecutor(provider_url=provider.url).execute(
            AttemptExecution(
                instance_id=attempt.instance_id,
                step_id=attempt.step_id,
                attempt_id=attempt.attempt_id,
                attempt_number=attempt.attempt_number,
                template_key=attempt.template_key,
                executor_key=attempt.executor_key,
                input=attempt.input,
            ),
            CancellationToken(),
        )
        provider_observation = RenewalEffectObservation(
            classification=observation.value["classification"],
            provider_request_id=str(observation.value["provider_request_id"]),
        )
        accept = AcceptRenewalEffectObservation(
            command_id=effect_observation_command_id(attempt.attempt_id),
            actor=Actor("system", "email"),
            cause=Cause("attempt", str(attempt.attempt_id)),
            input=AcceptRenewalEffectObservationInput(
                attempt,
                "email",
                provider_observation,
            ),
        )
        receipt = application.accept_renewal_effect_observation(accept)
        receipt_replay = application.accept_renewal_effect_observation(accept)

        assert permit_replay == permit
        assert receipt_replay == receipt
        assert receipt.result.template_key == "send_renewal_email"


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
