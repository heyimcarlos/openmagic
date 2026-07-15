from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from example_insurance.migrations import apply_migrations
from example_insurance.renewals import (
    ExampleInsurance,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
)
from openmagic_evals.harness import TestDeployment
from openmagic_evals.harness._postgres import postgres_container
from openmagic_runtime.commands import Actor, Cause, IdempotencyConflict, InvalidCommand
from openmagic_runtime.delivery import (
    ClaimDelivery,
    acknowledge_delivery,
    claim_delivery_once,
)
from openmagic_runtime.kernel.control import KernelControl, StartInstance, start_instance
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.kernel.work import AttemptResultConflict, DispositionRequired
from openmagic_runtime.threads import CreateThread, ThreadStore


def test_start_command_commits_and_replays_value_identically() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        thread = ThreadStore(database_url=database_url).create(
            CreateThread(
                thread_id=uuid4(),
                channel_kind="email",
                channel_reference="broker-conversation-17",
            )
        )
        command = StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-2048",
                policyholder_name="Avery Chen",
                renewal_date="2027-01-31",
                expiring_premium_cents=125_000,
            ),
        )

        first = application.start_renewal_outreach(command)
        replay = application.start_renewal_outreach(command)
        conflict = replace(
            command,
            input=replace(command.input, expiring_premium_cents=126_000),
        )
        snapshot = KernelInspection(database_url=database_url).snapshot(first.result.instance_id)

        with pytest.raises(IdempotencyConflict):
            application.start_renewal_outreach(conflict)
        assert replay == first
        assert first.result.workflow_id == command.input.workflow_id
        assert first.result.thread_id == thread.thread_id
        assert snapshot.definition_key == "example_insurance.renewal_outreach"
        assert snapshot.definition_version == 1
        assert snapshot.state == "open"
        assert [(step.template_key, step.state) for step in snapshot.steps] == [
            ("gather_renewal_facts", "pending")
        ]
        assert snapshot.waits == ()
        assert isinstance(first.result.instance_id, UUID)


def test_command_validation_rejects_nested_types_and_semantics_before_commit() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        thread = ThreadStore(database_url=database_url).create(
            CreateThread(uuid4(), "email", "broker-command-validation")
        )
        command_id = uuid4()
        invalid_type = StartRenewalOutreach(
            command_id=command_id,
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-TYPE",
                policyholder_name="Validation",
                renewal_date="2027-01-31",
                expiring_premium_cents="100000",  # type: ignore[arg-type]
            ),
        )
        invalid_semantics = replace(
            invalid_type,
            input=replace(invalid_type.input, expiring_premium_cents=-1),
        )
        corrected = replace(
            invalid_type,
            input=replace(invalid_type.input, expiring_premium_cents=100_000),
        )

        with pytest.raises(InvalidCommand):
            application.start_renewal_outreach(invalid_type)
        with pytest.raises(InvalidCommand):
            application.start_renewal_outreach(invalid_semantics)
        receipt = application.start_renewal_outreach(corrected)

        assert receipt.command_id == command_id


def test_exact_attempt_result_replay_does_not_repeat_route_effects() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        thread = ThreadStore(database_url=database_url).create(
            CreateThread(uuid4(), "email", "broker-result-replay")
        )
        command = StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-REPLAY",
                policyholder_name="Replay Test",
                renewal_date="2027-03-31",
                expiring_premium_cents=100_000,
            ),
        )
        started = application.start_renewal_outreach(command)
        attempt = application.claim_workflow_attempt(
            worker_id="workflow-replay",
            claim_request_id=uuid4(),
        )
        assert attempt is not None
        observation = {
            "policy_number": "OM-REPLAY",
            "policyholder_name": "Replay Test",
            "renewal_date": "2027-03-31",
            "expiring_premium_cents": 100_000,
        }

        first = application.submit_workflow_observation(
            attempt=attempt,
            worker_id="workflow-replay",
            observation=observation,
        )
        watermark = (
            KernelInspection(database_url=database_url)
            .snapshot(started.result.instance_id)
            .observed_through_sequence
        )
        replay = application.submit_workflow_observation(
            attempt=attempt,
            worker_id="workflow-replay",
            observation=observation,
        )

        assert replay == first
        assert replay.steps == first.steps
        assert replay.waits == first.waits
        assert (
            KernelInspection(database_url=database_url)
            .snapshot(started.result.instance_id)
            .observed_through_sequence
            == watermark
        )
        with pytest.raises(AttemptResultConflict):
            application.submit_workflow_observation(
                attempt=attempt,
                worker_id="workflow-replay",
                observation={**observation, "policy_number": "OTHER"},
            )
        conflicting_disposition = DispositionRequired(
            instance_id=attempt.instance_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            attempt_number=attempt.attempt_number,
            template_key=attempt.template_key,
            observation=observation,
            consumed=True,
            replayed=True,
        )
        with (
            psycopg.connect(database_url) as connection,
            connection.transaction(),
            pytest.raises(ValueError, match="conflicting input"),
        ):
            KernelControl(connection).succeed(
                conflicting_disposition,
                output=observation,
                outcome_route="draft_after_facts",
                route_input={
                    "workflow_id": str(command.input.workflow_id),
                    "thread_id": str(thread.thread_id),
                    **observation,
                    "policy_number": "OTHER",
                },
            )


def test_workflow_worker_uses_one_executor_seam_for_facts_and_agent_draft() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        thread = ThreadStore(database_url=database_url).create(
            CreateThread(
                thread_id=uuid4(),
                channel_kind="email",
                channel_reference="broker-conversation-18",
            )
        )
        command = StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-4096",
                policyholder_name="Morgan Lee",
                renewal_date="2027-02-28",
                expiring_premium_cents=198_500,
            ),
        )
        started = application.start_renewal_outreach(command)

        first_attempt = application.run_workflow_worker_once(worker_id="workflow-a")
        after_facts = KernelInspection(database_url=database_url).snapshot(
            started.result.instance_id
        )
        second_attempt = application.run_workflow_worker_once(worker_id="workflow-a")
        after_draft = KernelInspection(database_url=database_url).snapshot(
            started.result.instance_id
        )

        assert first_attempt is not None
        assert first_attempt.template_key == "gather_renewal_facts"
        assert second_attempt is not None
        assert second_attempt.template_key == "draft_renewal_email"
        assert [(step.template_key, step.state) for step in after_facts.steps] == [
            ("gather_renewal_facts", "succeeded"),
            ("draft_renewal_email", "pending"),
        ]
        assert [(step.template_key, step.state) for step in after_draft.steps] == [
            ("gather_renewal_facts", "succeeded"),
            ("draft_renewal_email", "succeeded"),
        ]
        assert [(wait.template_key, wait.state) for wait in after_draft.waits] == [
            ("renewal_draft_approval", "unsatisfied")
        ]
        assert first_attempt.executor_key == "example_insurance.renewal_facts.v1"
        assert second_attempt.executor_key == "example_insurance.renewal_draft_agent.v1"
        assert second_attempt.agent_run_id is not None
        assert second_attempt.agent_runtime_generation == 1


def test_agent_attempt_replay_uses_one_durable_run_without_reexecution() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        thread = ThreadStore(database_url=database_url).create(
            CreateThread(uuid4(), "email", "broker-agent-replay")
        )
        command = StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-AGENT-REPLAY",
                policyholder_name="Agent Replay",
                renewal_date="2027-05-31",
                expiring_premium_cents=300_000,
            ),
        )
        application.start_renewal_outreach(command)
        application.run_workflow_worker_once(worker_id="workflow-agent-replay")
        draft_attempt = application.claim_workflow_attempt(
            worker_id="workflow-agent-replay",
            claim_request_id=uuid4(),
        )
        assert draft_attempt is not None

        first = application.complete_workflow_attempt(
            attempt=draft_attempt,
            worker_id="workflow-agent-replay",
        )
        replay = application.complete_workflow_attempt(
            attempt=draft_attempt,
            worker_id="workflow-agent-replay",
        )
        evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))

        assert replay == first
        assert len(evidence["correlations"]["agent_run_ids"]) == 1
        assert evidence["outcomes"]["agent_run_states"] == ["completed"]


def test_start_route_replay_returns_the_same_complete_occurrence_batch() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        ExampleInsurance(database_url=database_url).prepare()
        command_id = uuid4()
        request = StartInstance(
            command_id=command_id,
            definition_key="example_insurance.renewal_outreach",
            definition_version=1,
            instance_input={
                "workflow_id": str(uuid4()),
                "thread_id": str(uuid4()),
                "policy_id": str(uuid4()),
            },
            route_input={
                "policy_id": str(uuid4()),
                "policy_number": "OM-ROUTE-1",
                "policyholder_name": "Route Replay",
                "renewal_date": "2027-06-30",
                "expiring_premium_cents": 100_000,
            },
        )

        first = start_instance(database_url=database_url, request=request)
        replay = start_instance(database_url=database_url, request=request)
        snapshot = KernelInspection(database_url=database_url).snapshot(first.instance_id)

        assert replay == first
        assert len(first.steps) == 1
        assert first.waits == {}
        assert snapshot.observed_through_sequence == 1


def test_competing_command_and_step_claims_preserve_cardinality_one() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        thread = ThreadStore(database_url=database_url).create(
            CreateThread(uuid4(), "email", "broker-conversation-command-race")
        )
        command = StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-RACE-1",
                policyholder_name="Casey Nguyen",
                renewal_date="2027-07-31",
                expiring_premium_cents=512_000,
            ),
        )
        command_barrier = Barrier(2)

        def submit_command() -> object:
            command_barrier.wait()
            return application.start_renewal_outreach(command)

        with ThreadPoolExecutor(max_workers=2) as executor:
            command_results = tuple(executor.map(lambda _: submit_command(), range(2)))

        claim_barrier = Barrier(2)

        def claim(worker_id: str) -> object:
            claim_barrier.wait()
            return application.claim_workflow_attempt(
                worker_id=worker_id,
                claim_request_id=uuid4(),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = tuple(executor.map(claim, ("workflow-a", "workflow-b")))

        assert command_results[0] == command_results[1]
        assert sum(item is not None for item in claims) == 1


def test_stale_workflow_result_and_wrong_thread_delivery_proposal_are_rejected() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        threads = ThreadStore(database_url=database_url)
        intended = threads.create(CreateThread(uuid4(), "email", "broker-stale-intended"))
        wrong = threads.create(CreateThread(uuid4(), "email", "broker-stale-wrong"))
        command = StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=intended.thread_id,
                policy_id=uuid4(),
                policy_number="OM-FENCE-1",
                policyholder_name="Jordan Ali",
                renewal_date="2027-08-31",
                expiring_premium_cents=618_000,
            ),
        )
        application.start_renewal_outreach(command)
        stale = application.claim_workflow_attempt(
            worker_id="lost-worker",
            claim_request_id=uuid4(),
        )
        assert stale is not None
        time.sleep(1.1)
        assert application.recover_expired_workflow_attempt()
        replacement = application.claim_workflow_attempt(
            worker_id="replacement-worker",
            claim_request_id=uuid4(),
        )
        assert replacement is not None

        forged = replace(
            replacement,
            template_key="draft_renewal_email",
            executor_key="example_insurance.renewal_draft_agent.v1",
        )
        with pytest.raises(RuntimeError, match="durable Attempt authority"):
            application.submit_workflow_observation(
                attempt=forged,
                worker_id="replacement-worker",
                observation={"subject": "forged", "body": "forged"},
            )

        with pytest.raises(RuntimeError, match="stale"):
            application.complete_workflow_attempt(attempt=stale, worker_id="lost-worker")
        application.complete_workflow_attempt(
            attempt=replacement,
            worker_id="replacement-worker",
        )
        application.run_workflow_worker_once(worker_id="replacement-worker")
        delivery = claim_delivery_once(
            database_url=database_url,
            request=ClaimDelivery(uuid4(), "delivery-worker"),
        )
        assert delivery is not None

        with pytest.raises(RuntimeError, match="wrong exact Thread"):
            acknowledge_delivery(
                database_url=database_url,
                claim=delivery,
                worker_id="delivery-worker",
                proposed_thread_id=wrong.thread_id,
            )
        acknowledgement = acknowledge_delivery(
            database_url=database_url,
            claim=delivery,
            worker_id="delivery-worker",
            proposed_thread_id=intended.thread_id,
        )

        assert acknowledgement.thread_id == intended.thread_id
        assert threads.read(wrong.thread_id).messages == ()


@pytest.mark.integration
def test_seeded_step_and_delivery_claim_races_hold_cardinality_one_100_times() -> None:
    seeds = tuple(range(100))
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        thread = ThreadStore(database_url=database_url).create(
            CreateThread(uuid4(), "email", "broker-cardinality-races")
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            for seed in seeds:
                command = StartRenewalOutreach(
                    command_id=uuid4(),
                    actor=Actor(kind="party", identifier=str(uuid4())),
                    cause=Cause(kind="message", identifier=str(uuid4())),
                    input=StartRenewalOutreachInput(
                        workflow_id=uuid4(),
                        thread_id=thread.thread_id,
                        policy_id=uuid4(),
                        policy_number=f"OM-RACE-{seed}",
                        policyholder_name=f"Seed {seed}",
                        renewal_date="2027-10-31",
                        expiring_premium_cents=800_000 + seed,
                    ),
                )
                application.start_renewal_outreach(command)
                step_barrier = Barrier(2)

                def claim_step(
                    index: int,
                    barrier: Barrier = step_barrier,
                    race_seed: int = seed,
                ) -> object:
                    barrier.wait()
                    time.sleep(random.Random(race_seed * 2 + index).random() / 1000)
                    return application.claim_workflow_attempt(
                        worker_id=f"workflow-{index}",
                        claim_request_id=uuid4(),
                    )

                step_claims = tuple(executor.map(claim_step, range(2)))
                step_winners = [item for item in step_claims if item is not None]
                assert len(step_winners) == 1, f"Step claim seed {seed}"
                step_worker = "workflow-0" if step_claims[0] is not None else "workflow-1"
                application.complete_workflow_attempt(
                    attempt=step_winners[0],
                    worker_id=step_worker,
                )
                application.run_workflow_worker_once(worker_id="workflow-draft")

                delivery_barrier = Barrier(2)

                def claim_delivery_race(
                    index: int,
                    barrier: Barrier = delivery_barrier,
                    race_seed: int = seed,
                ) -> object:
                    barrier.wait()
                    time.sleep(random.Random(race_seed * 2 + index + 10_000).random() / 1000)
                    return application.claim_delivery_attempt(
                        worker_id=f"delivery-{index}",
                        claim_request_id=uuid4(),
                    )

                delivery_claims = tuple(executor.map(claim_delivery_race, range(2)))
                assert sum(item is not None for item in delivery_claims) == 1, (
                    f"Delivery claim seed {seed}"
                )
                delivery_winner = next(item for item in delivery_claims if item is not None)
                delivery_worker = "delivery-0" if delivery_claims[0] is not None else "delivery-1"
                application.complete_delivery_attempt(
                    claim=delivery_winner,
                    worker_id=delivery_worker,
                )

    assert seeds == tuple(range(100))


def test_delivery_appends_once_to_only_the_frozen_exact_thread() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        application = ExampleInsurance(database_url=database_url)
        application.prepare()
        threads = ThreadStore(database_url=database_url)
        intended = threads.create(CreateThread(uuid4(), "email", "broker-conversation-intended"))
        other = threads.create(CreateThread(uuid4(), "email", "broker-conversation-other"))
        command = StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=intended.thread_id,
                policy_id=uuid4(),
                policy_number="OM-8192",
                policyholder_name="Taylor Singh",
                renewal_date="2027-03-31",
                expiring_premium_cents=211_000,
            ),
        )
        application.start_renewal_outreach(command)
        application.run_workflow_worker_once(worker_id="workflow-a")
        application.run_workflow_worker_once(worker_id="workflow-a")

        claim_barrier = Barrier(2)

        def claim_delivery(worker_id: str) -> object:
            claim_barrier.wait()
            return application.claim_delivery_attempt(
                worker_id=worker_id,
                claim_request_id=uuid4(),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = tuple(executor.map(claim_delivery, ("delivery-a", "delivery-b")))
        winners = [item for item in claims if item is not None]
        assert len(winners) == 1
        forged_content_claim = replace(
            winners[0],
            content_descriptor={"input": {"subject": "forged", "body": "forged"}},
        )
        acknowledgement = application.complete_delivery_attempt(
            claim=forged_content_claim,
            worker_id="delivery-a" if claims[0] is not None else "delivery-b",
        )
        replay = application.replay_delivery_acknowledgement(
            delivery_attempt_id=acknowledgement.delivery_attempt_id
        )
        intended_thread = threads.read(intended.thread_id)
        other_thread = threads.read(other.thread_id)

        assert replay == acknowledgement
        assert acknowledgement.thread_id == intended.thread_id
        assert acknowledgement.message_sequence == 1
        assert len(intended_thread.messages) == 1
        assert intended_thread.messages[0].message_id == acknowledgement.message_id
        assert "OM-8192" in intended_thread.messages[0].content
        assert "forged" not in intended_thread.messages[0].content
        assert other_thread.messages == ()


def test_fresh_worker_processes_recover_the_complete_sanitized_evidence_chain(tmp_path) -> None:
    with TestDeployment(working_directory=tmp_path) as deployment:
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(database_url=deployment.database_url)
        application.prepare()
        thread = ThreadStore(database_url=deployment.database_url).create(
            CreateThread(uuid4(), "email", "broker-conversation-process-recovery")
        )
        command = StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-16384",
                policyholder_name="Jamie Patel",
                renewal_date="2027-04-30",
                expiring_premium_cents=305_500,
            ),
        )
        application.start_renewal_outreach(command)

        workflow_process = deployment.restart_role("workflow-worker")
        _wait_for_outcome(
            application, command.input.workflow_id, "approval_wait_state", "unsatisfied"
        )
        deployment.terminate_role("workflow-worker")
        replacement_workflow_process = deployment.restart_role("workflow-worker")
        delivery_process = deployment.restart_role("delivery-worker")
        evidence = _wait_for_outcome(
            application,
            command.input.workflow_id,
            "delivery_state",
            "delivered",
        )

        assert workflow_process.pid != replacement_workflow_process.pid
        assert delivery_process.pid not in {workflow_process.pid, replacement_workflow_process.pid}
        assert evidence["schema_version"] == "openmagic.evidence.v1"
        assert evidence["scenario"] == "renewal_drafting"
        assert evidence["redacted"] is True
        assert evidence["invariant_violations"] == []
        assert evidence["outcomes"]["external_email_effect_count"] == 0
        assert evidence["outcomes"]["approval_wait_state"] == "unsatisfied"
        assert set(evidence["correlations"]) == {
            "agent_run_id",
            "agent_run_ids",
            "attempt_ids",
            "command_id",
            "delivery_id",
            "domain_event_id",
            "instance_id",
            "message_id",
            "step_ids",
            "thread_id",
            "workflow_id",
        }


def test_process_loss_after_claim_is_recovered_and_fenced_by_a_fresh_process(tmp_path) -> None:
    with TestDeployment(working_directory=tmp_path) as deployment:
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(database_url=deployment.database_url)
        application.prepare()
        thread = ThreadStore(database_url=deployment.database_url).create(
            CreateThread(uuid4(), "email", "broker-conversation-claim-loss")
        )
        command = StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-32768",
                policyholder_name="Riley Brooks",
                renewal_date="2027-05-31",
                expiring_premium_cents=411_000,
            ),
        )
        application.start_renewal_outreach(command)

        lost_process = deployment.restart_role("workflow-worker")
        _wait_for_attempt_state(application, command.input.workflow_id, "leased")
        deployment.terminate_role("workflow-worker")
        time.sleep(1.1)
        recovery_process = deployment.restart_role("workflow-worker")
        evidence = _wait_for_outcome(
            application,
            command.input.workflow_id,
            "approval_wait_state",
            "unsatisfied",
        )

        assert recovery_process.pid != lost_process.pid
        assert evidence["outcomes"]["attempt_states"].count("abandoned") == 1
        assert evidence["outcomes"]["attempt_states"].count("completed") == 2
        assert len(evidence["correlations"]["attempt_ids"]) == 3
        assert evidence["invariant_violations"] == []


def test_agent_process_loss_terminalizes_run_and_retries_without_phantom_authority(
    tmp_path,
) -> None:
    with TestDeployment(working_directory=tmp_path) as deployment:
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(database_url=deployment.database_url)
        application.prepare()
        thread = ThreadStore(database_url=deployment.database_url).create(
            CreateThread(uuid4(), "email", "broker-agent-process-loss")
        )
        command = StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-AGENT-LOSS",
                policyholder_name="Agent Loss",
                renewal_date="2027-11-30",
                expiring_premium_cents=910_000,
            ),
        )
        application.start_renewal_outreach(command)
        application.run_workflow_worker_once(worker_id="workflow-facts")
        with psycopg.connect(deployment.database_url) as connection, connection.transaction():
            connection.execute(
                "CREATE FUNCTION example_insurance.pause_draft_insert() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(5); RETURN NEW; END $$"
            )
            connection.execute(
                "CREATE TRIGGER pause_draft_insert BEFORE INSERT ON "
                "example_insurance.renewal_drafts FOR EACH ROW EXECUTE FUNCTION "
                "example_insurance.pause_draft_insert()"
            )

        lost_process = deployment.restart_role("workflow-worker")
        deadline = time.monotonic() + 10
        paused = False
        while time.monotonic() < deadline:
            with psycopg.connect(deployment.database_url) as connection:
                active = connection.execute(
                    "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() "
                    "AND state = 'active' AND query LIKE "
                    "'INSERT INTO example_insurance.renewal_drafts%%'"
                ).fetchone()
            if active is not None:
                paused = True
                break
            time.sleep(0.02)
        assert paused, "Agent draft transaction did not reach the process-loss seam"
        deployment.terminate_role("workflow-worker")
        with psycopg.connect(deployment.database_url) as connection, connection.transaction():
            connection.execute(
                "DROP TRIGGER pause_draft_insert ON example_insurance.renewal_drafts"
            )
            connection.execute("DROP FUNCTION example_insurance.pause_draft_insert()")
        time.sleep(1.1)

        recovery_process = deployment.restart_role("workflow-worker")
        evidence = _wait_for_outcome(
            application,
            command.input.workflow_id,
            "approval_wait_state",
            "unsatisfied",
        )

        assert recovery_process.pid != lost_process.pid
        assert evidence["outcomes"]["agent_run_states"] == ["abandoned", "completed"]
        assert "running" not in evidence["outcomes"]["agent_run_states"]
        assert len(evidence["correlations"]["agent_run_ids"]) == 2


def test_delivery_process_loss_after_claim_recovers_without_duplicate_message(tmp_path) -> None:
    with TestDeployment(working_directory=tmp_path) as deployment:
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        application = ExampleInsurance(database_url=deployment.database_url)
        application.prepare()
        threads = ThreadStore(database_url=deployment.database_url)
        thread = threads.create(CreateThread(uuid4(), "email", "broker-delivery-claim-loss"))
        command = StartRenewalOutreach(
            command_id=uuid4(),
            actor=Actor(kind="party", identifier=str(uuid4())),
            cause=Cause(kind="message", identifier=str(uuid4())),
            input=StartRenewalOutreachInput(
                workflow_id=uuid4(),
                thread_id=thread.thread_id,
                policy_id=uuid4(),
                policy_number="OM-65536",
                policyholder_name="Quinn Martin",
                renewal_date="2027-09-30",
                expiring_premium_cents=720_000,
            ),
        )
        application.start_renewal_outreach(command)
        application.run_workflow_worker_once(worker_id="workflow-direct")
        application.run_workflow_worker_once(worker_id="workflow-direct")

        with psycopg.connect(deployment.database_url) as connection:
            connection.execute(
                "CREATE FUNCTION openmagic_runtime.eval_pause_message_append() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "PERFORM pg_sleep(10); RETURN NEW; END $$"
            )
            connection.execute(
                "CREATE TRIGGER eval_pause_message_append AFTER INSERT "
                "ON openmagic_runtime.messages FOR EACH ROW EXECUTE FUNCTION "
                "openmagic_runtime.eval_pause_message_append()"
            )

        lost_process = deployment.restart_role("delivery-worker")
        _wait_for_append_pause(deployment.database_url)
        deployment.terminate_role("delivery-worker")
        with psycopg.connect(deployment.database_url) as connection:
            connection.execute(
                "DROP TRIGGER eval_pause_message_append ON openmagic_runtime.messages"
            )
            connection.execute("DROP FUNCTION openmagic_runtime.eval_pause_message_append()")
        time.sleep(1.1)
        recovery_process = deployment.restart_role("delivery-worker")
        evidence = _wait_for_outcome(
            application,
            command.input.workflow_id,
            "delivery_state",
            "delivered",
        )

        assert recovery_process.pid != lost_process.pid
        assert evidence["outcomes"]["delivery_attempt_states"] == [
            "abandoned",
            "succeeded",
        ]
        assert len(threads.read(thread.thread_id).messages) == 1
        assert evidence["invariant_violations"] == []


def _wait_for_outcome(
    application: ExampleInsurance,
    workflow_id: UUID,
    key: str,
    expected: str,
) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        evidence = json.loads(application.renewal_evidence_json(workflow_id))
        outcomes = evidence["outcomes"]
        if outcomes[key] == expected:
            return evidence
        time.sleep(0.05)
    raise AssertionError(f"renewal evidence did not reach {key}={expected}")


def _wait_for_attempt_state(
    application: ExampleInsurance,
    workflow_id: UUID,
    expected: str,
) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        evidence = json.loads(application.renewal_evidence_json(workflow_id))
        if expected in evidence["outcomes"]["attempt_states"]:
            return evidence
        time.sleep(0.005)
    raise AssertionError(f"renewal evidence did not contain Attempt state {expected}")


def _wait_for_delivery_attempt_state(
    application: ExampleInsurance,
    workflow_id: UUID,
    expected: str,
) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        evidence = json.loads(application.renewal_evidence_json(workflow_id))
        if expected in evidence["outcomes"]["delivery_attempt_states"]:
            return evidence
        time.sleep(0.005)
    raise AssertionError(f"renewal evidence did not contain Delivery Attempt state {expected}")


def _wait_for_append_pause(database_url: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with psycopg.connect(database_url) as connection:
            paused = connection.execute(
                "SELECT 1 FROM pg_stat_activity WHERE query LIKE "
                "'INSERT INTO openmagic_runtime.messages%%' AND wait_event = 'PgSleep'"
            ).fetchone()
        if paused is not None:
            return
        time.sleep(0.01)
    raise AssertionError("Delivery Worker did not reach the pre-acknowledgement pause")
