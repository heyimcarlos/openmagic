from __future__ import annotations

import json
from dataclasses import replace
from urllib.request import Request, urlopen
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
from example_insurance.renewal_effect_types import (
    ExternalEffectPermit,
    RenewalEmailEffect,
    logical_effect_id,
)
from example_insurance.renewal_effects import (
    AuthorizedEmailEffectExecutor,
    EmailProviderClient,
    committed_permit_execution_input,
)
from example_insurance.renewals import (
    ExampleInsurance,
)
from openmagic_evals.evidence.case_recording import record_renewal_case
from openmagic_evals.harness import (
    LocalEmailProvider,
    approve_renewal,
    prepare_renewal_approval,
)
from openmagic_evals.harness._postgres import postgres_container
from openmagic_runtime.commands import Actor, Cause
from openmagic_runtime.evidence import content_fingerprint
from openmagic_runtime.execution import AttemptExecution, CancellationToken
from openmagic_runtime.kernel.control import KernelControl, ResolveDeferredStep
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.threads import ThreadStore


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
            presentation = application.renewal_approval_presentation(command.input.workflow_id)
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
            approved_event = next(
                event
                for event in evidence["outcomes"]["domain_events"]
                if event["event_type"] == "renewal.draft.approved"
            )
            completed_event = next(
                event
                for event in evidence["outcomes"]["domain_events"]
                if event["event_type"] == "renewal.outreach.completed"
            )
            grant = evidence["outcomes"]["approval_grants"][0]
            effect = evidence["outcomes"]["external_effects"][0]
            applied_evidence = evidence["outcomes"]["effect_evidence"][-1]
            assert decision["signal_id"] in evidence["correlations"]["signal_ids"]
            assert approved_event["cause"] == {
                "kind": "command",
                "identifier": decision["command_id"],
            }
            assert completed_event["actor"] == {"kind": "system", "identifier": "email"}
            assert completed_event["cause"] == {
                "kind": "command",
                "identifier": str(effect_observation_command_id(result.attempt_id)),
            }
            assert decision["presented_message_id"] == str(presentation.message_id)
            assert decision["thread_sequence"] == presentation.thread_sequence
            assert decision["message_fingerprint"] == presentation.message_fingerprint
            assert grant["decision_id"] == decision["decision_id"]
            assert effect["approval_grant_id"] == grant["approval_grant_id"]
            assert applied_evidence["logical_effect_id"] == effect["logical_effect_id"]
            record_renewal_case(
                case_id="completion.evidence-backed",
                scenario_id="accepted-evidence",
                application=application,
                database_url=database_url,
                workflow_id=command.input.workflow_id,
                document={
                    "instance_state": snapshot.state,
                    "completion_event_type": completed_event["event_type"],
                    "provider_requests": len(requests),
                },
                worker_ids=("email",),
                process_ids=(provider.pid,),
                provider_request_ids=(str(requests[0]["provider_request_id"]),),
            )
            record_renewal_case(
                case_id="domain-event.atomic-correlation",
                scenario_id="success",
                application=application,
                database_url=database_url,
                workflow_id=command.input.workflow_id,
                document={
                    "event_id": completed_event["event_id"],
                    "event_type": completed_event["event_type"],
                    "instance_state": snapshot.state,
                    "source_command_id": completed_event["cause"]["identifier"],
                },
                additional_command_ids=(UUID(completed_event["cause"]["identifier"]),),
                domain_event_ids=(UUID(completed_event["event_id"]),),
            )


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
            record_renewal_case(
                case_id="retry.finite-policy",
                scenario_id="safe-retry-schedule",
                application=application,
                database_url=database_url,
                workflow_id=command.input.workflow_id,
                document={
                    "provider_behaviors": [request["behavior"] for request in requests],
                    "same_idempotency_key": requests[0]["idempotency_key"]
                    == requests[1]["idempotency_key"],
                    "instance_state": completed.state,
                },
                worker_ids=("email",),
                process_ids=(provider.pid,),
                provider_request_ids=tuple(
                    str(request["provider_request_id"]) for request in requests
                ),
            )


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
            evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))
            failure_events = tuple(
                item
                for item in evidence["outcomes"]["domain_events"]
                if item["event_type"] == "external_effect.not_applied"
            )
            assert len(failure_events) == 3
            assert all(item["cause"]["kind"] == "command" for item in failure_events)
            record_renewal_case(
                case_id="retry.finite-policy",
                scenario_id="exhausted-budget",
                application=application,
                database_url=database_url,
                workflow_id=command.input.workflow_id,
                document={
                    "attempt_count": len(provider.requests()),
                    "terminal_step_state": snapshot.steps[-1].state,
                    "reconciliation_materialized": False,
                },
                worker_ids=("email",),
                process_ids=(provider.pid,),
            )
            record_renewal_case(
                case_id="domain-event.atomic-correlation",
                scenario_id="failure",
                application=application,
                database_url=database_url,
                workflow_id=command.input.workflow_id,
                document={
                    "terminal_step_state": snapshot.steps[-1].state,
                    "event_ids": [item["event_id"] for item in failure_events],
                    "event_types": [item["event_type"] for item in failure_events],
                    "source_command_ids": [item["cause"]["identifier"] for item in failure_events],
                },
                additional_command_ids=tuple(
                    UUID(item["cause"]["identifier"]) for item in failure_events
                ),
                domain_event_ids=tuple(UUID(item["event_id"]) for item in failure_events),
            )
            record_renewal_case(
                case_id="external-effect.fenced-uncertainty",
                scenario_id="provider-failure",
                application=application,
                database_url=database_url,
                workflow_id=command.input.workflow_id,
                document={
                    "provider_attempts": len(provider.requests()),
                    "terminal_step_state": snapshot.steps[-1].state,
                },
                process_ids=(provider.pid,),
            )


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
            requests = provider.requests()
            record_renewal_case(
                case_id="external-effect.fenced-uncertainty",
                scenario_id="response-loss",
                application=application,
                database_url=database_url,
                workflow_id=command.input.workflow_id,
                document={
                    "requests_before_reconciliation": len(requests_before_reconciliation),
                    "requests_after_reconciliation": len(requests),
                    "provider_restarted": provider.pid != previous_provider_pid,
                },
                process_ids=(previous_provider_pid, provider.pid),
                provider_request_ids=(str(requests[0]["provider_request_id"]),),
            )
            record_renewal_case(
                case_id="external-effect.fenced-uncertainty",
                scenario_id="reconciliation",
                application=application,
                database_url=database_url,
                workflow_id=command.input.workflow_id,
                document={
                    "instance_state": completed.state,
                    "redispatch_count": len(requests) - 1,
                },
                process_ids=(previous_provider_pid, provider.pid),
            )
    finally:
        provider.stop()


def test_provider_reuses_one_result_for_duplicate_idempotency_identity(tmp_path) -> None:
    with LocalEmailProvider(working_directory=tmp_path / "provider") as provider:
        provider.configure(behaviors=("success",))
        effect_id = str(uuid4())
        payload = json.dumps(
            {
                "logical_effect_id": effect_id,
                "idempotency_key": effect_id,
                "recipient_email": "duplicate@example.test",
                "subject": "Duplicate identity",
                "body": "One logical email",
            }
        ).encode()
        for _ in range(2):
            with urlopen(
                Request(
                    f"{provider.url}/dispatch",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=2,
            ) as response:
                assert json.load(response)["classification"] == "applied"

        requests = provider.requests()
        assert len(requests) == 2
        assert requests[0]["idempotency_key"] == requests[1]["idempotency_key"]
        assert [request["duplicate"] for request in requests] == [0, 1]


def test_email_provider_client_dispatches_typed_permit_without_database(tmp_path) -> None:
    with LocalEmailProvider(working_directory=tmp_path / "provider") as provider:
        provider.configure(behaviors=("success",))
        step_id = uuid4()
        effect = RenewalEmailEffect(
            "typed@example.test",
            "Typed permit",
            "Database-independent provider input",
        )
        effect_id = logical_effect_id(step_id)
        permit = ExternalEffectPermit(
            logical_effect_id=effect_id,
            step_id=step_id,
            attempt_id=uuid4(),
            provider_idempotency_key=str(effect_id),
            effect_fingerprint=content_fingerprint(effect),
            effect=effect,
        )

        observation = EmailProviderClient(provider_url=provider.url).execute(
            permit,
            CancellationToken(),
        )

        assert observation.value["classification"] == "applied"
        assert provider.requests()[0]["recipient_email"] == "typed@example.test"


def test_provider_executor_rejects_mismatched_permit_bound_input(tmp_path) -> None:
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
        attempt = application.claim_workflow_attempt(worker_id="email", claim_request_id=uuid4())
        assert attempt is not None
        receipt = application.authorize_email_dispatch(attempt=attempt, worker_id="email")
        execution = AttemptExecution(
            instance_id=attempt.instance_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            attempt_number=attempt.attempt_number,
            template_key=attempt.template_key,
            executor_key=attempt.executor_key,
            input=committed_permit_execution_input(receipt),
        )
        forged_effect = RenewalEmailEffect(
            "tampered@example.test",
            receipt.result.effect.subject,
            receipt.result.effect.body,
        )
        forged_result = {
            "logical_effect_id": str(receipt.result.logical_effect_id),
            "step_id": str(receipt.result.step_id),
            "attempt_id": str(receipt.result.attempt_id),
            "provider_idempotency_key": receipt.result.provider_idempotency_key,
            "effect_fingerprint": content_fingerprint(forged_effect),
            "effect": {
                "recipient_email": forged_effect.recipient_email,
                "subject": forged_effect.subject,
                "body": forged_effect.body,
            },
        }
        mismatches = (
            {"authorization_command_id": str(uuid4())},
            {"authorization_result_digest": "wrong"},
            {"authorization_result_digest": content_fingerprint(forged_result)},
            {"recipient_email": forged_effect.recipient_email},
        )

        for mismatch in mismatches:
            with pytest.raises(RuntimeError, match="permit"):
                AuthorizedEmailEffectExecutor(
                    database_url=database_url,
                    client=EmailProviderClient(provider_url=provider.url),
                ).execute(
                    replace(execution, input={**execution.input, **mismatch}),
                    CancellationToken(),
                )

        assert provider.requests() == ()


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
        observation = AuthorizedEmailEffectExecutor(
            database_url=database_url,
            client=EmailProviderClient(provider_url=provider.url),
        ).execute(
            AttemptExecution(
                instance_id=attempt.instance_id,
                step_id=attempt.step_id,
                attempt_id=attempt.attempt_id,
                attempt_number=attempt.attempt_number,
                template_key=attempt.template_key,
                executor_key=attempt.executor_key,
                input=committed_permit_execution_input(permit),
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
        evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))
        events = {event["event_type"]: event for event in evidence["outcomes"]["domain_events"]}

        assert permit_replay == permit
        assert receipt_replay == receipt
        assert receipt.result.template_key == "send_renewal_email"
        assert events["external_effect.dispatch_started"]["cause"] == {
            "kind": "command",
            "identifier": str(permit.command_id),
        }
        assert events["external_effect.applied"]["cause"] == {
            "kind": "command",
            "identifier": str(accept.command_id),
        }


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
