from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from threading import Barrier, Event, Thread
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from example_insurance.migrations import apply_migrations
from example_insurance.renewal_facts import StaleRenewalFacts
from example_insurance.renewals import (
    ExampleInsurance,
    RenewalFacts,
    StartRenewalOutreach,
    StartRenewalOutreachInput,
    StartRenewalOutreachResult,
)
from openmagic_evals.evidence.case_recording import (
    record_case_observation,
    record_renewal_case,
)
from openmagic_evals.evidence.contracts import Correlations
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.harness import TestDeployment
from openmagic_evals.harness._postgres import postgres_container
from openmagic_runtime.agents import (
    AgentAudience,
    AgentConfiguration,
    AgentExecutionInput,
    AgentField,
    AgentRecord,
    AgentRunInput,
    AgentTask,
)
from openmagic_runtime.commands import (
    Actor,
    Cause,
    CommandReceipt,
    IdempotencyConflict,
    InvalidCommand,
)
from openmagic_runtime.delivery import (
    ClaimDelivery,
    ClaimedDelivery,
    DeliveryControl,
    DeliveryRetryPolicy,
    StaleDeliveryAuthority,
    acknowledge_delivery,
    claim_delivery_once,
)
from openmagic_runtime.evidence import RuntimeEvidenceReader
from openmagic_runtime.execution import (
    AttemptExecution,
    CancellationToken,
    ExecutionAuthorityLost,
    FreshAgentExecutor,
    execute_with_renewable_authority,
)
from openmagic_runtime.kernel.control import KernelControl, StartInstance, start_instance
from openmagic_runtime.kernel.definitions import (
    DefinitionCatalog,
    DefinitionIdentity,
    FieldBinding,
    FieldContract,
    RetryPolicy,
    Route,
    RouteOutput,
    StepTemplate,
    WorkflowDefinition,
)
from openmagic_runtime.kernel.inspection import KernelInspection
from openmagic_runtime.kernel.work import (
    AttemptResultConflict,
    ClaimedAttempt,
    ClaimWork,
    DispositionRequired,
    KernelWork,
    StaleAuthority,
    claim_once,
    renew_once,
)
from openmagic_runtime.threads import AppendMessage, CreateThread, ThreadContext, ThreadStore
from psycopg import sql


@dataclass(frozen=True)
class _RenewalEvalContext:
    database_url: str
    application: ExampleInsurance
    threads: ThreadStore


def _prepared_renewal_context(database_url: str) -> _RenewalEvalContext:
    application = ExampleInsurance(database_url=database_url)
    application.prepare()
    return _RenewalEvalContext(
        database_url=database_url,
        application=application,
        threads=ThreadStore(database_url=database_url),
    )


@contextmanager
def _renewal_postgres_context() -> Iterator[_RenewalEvalContext]:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        yield _prepared_renewal_context(database_url)


def _record_command_facts(application: ExampleInsurance, command: StartRenewalOutreach) -> None:
    value = command.input
    application.replace_renewal_facts(
        RenewalFacts(
            policy_id=value.policy_id,
            policy_number=value.policy_number,
            policyholder_name=value.policyholder_name,
            policyholder_email=value.policyholder_email,
            renewal_date=value.renewal_date,
            expiring_premium_cents=value.expiring_premium_cents,
        )
    )


def _start_prepared(
    application: ExampleInsurance, command: StartRenewalOutreach
) -> CommandReceipt[StartRenewalOutreachResult]:
    _record_command_facts(application, command)
    return application.start_renewal_outreach(command)


def _renewal_command(
    *,
    thread_id: UUID,
    policy_number: str,
    policyholder_name: str,
    renewal_date: str,
    expiring_premium_cents: int,
) -> StartRenewalOutreach:
    return StartRenewalOutreach(
        command_id=uuid4(),
        actor=Actor(kind="party", identifier=str(uuid4())),
        cause=Cause(kind="message", identifier=str(uuid4())),
        input=StartRenewalOutreachInput(
            workflow_id=uuid4(),
            thread_id=thread_id,
            policy_id=uuid4(),
            policy_number=policy_number,
            policyholder_name=policyholder_name,
            policyholder_email="policyholder@example.test",
            renewal_date=renewal_date,
            expiring_premium_cents=expiring_premium_cents,
        ),
    )


def _single_step_definition(*, key: str, executor_key: str) -> WorkflowDefinition:
    contract = (FieldContract("subject_id", "uuid"),)
    return WorkflowDefinition(
        identity=DefinitionIdentity(key, 1),
        instance_input_contract=contract,
        step_templates=(
            StepTemplate(
                key="work",
                executor_key=executor_key,
                input_contract=contract,
                observation_contract=(FieldContract("result", "string"),),
                output_contract=(FieldContract("result", "string"),),
                lease_seconds=1,
                maximum_attempt_seconds=5,
                retry_policy=RetryPolicy(()),
            ),
        ),
        wait_templates=(),
        routes=(
            Route(
                key="start",
                activation="start",
                activation_contract=contract,
                outputs=(
                    RouteOutput(
                        slot="work",
                        kind="step",
                        template_key="work",
                        input_bindings=(FieldBinding("subject_id", "subject_id"),),
                    ),
                ),
            ),
        ),
    )


@dataclass(frozen=True)
class _SlowDraftCandidate:
    result: str


def _slow_draft_agent_factory() -> Callable[[AgentExecutionInput], _SlowDraftCandidate]:
    def run(execution: AgentExecutionInput) -> _SlowDraftCandidate:
        assert execution.run_input.configuration.agent_key == "example.renewable_draft"
        time.sleep(1.4)
        return _SlowDraftCandidate(result="drafted")

    return run


def test_claim_skips_older_instance_without_a_compatible_executor() -> None:
    with _renewal_postgres_context() as context:
        database_url = context.database_url
        catalog = DefinitionCatalog(database_url=database_url)
        catalog.register(
            _single_step_definition(
                key="example.incompatible_work",
                executor_key="example.incompatible.v1",
            )
        )
        catalog.register(
            _single_step_definition(
                key="example.compatible_work",
                executor_key="example.compatible.v1",
            )
        )
        subject_id = str(uuid4())
        older = start_instance(
            database_url=database_url,
            request=StartInstance(
                command_id=uuid4(),
                definition_key="example.incompatible_work",
                definition_version=1,
                instance_input={"subject_id": subject_id},
                route_input={"subject_id": subject_id},
            ),
        )
        time.sleep(0.01)
        compatible = start_instance(
            database_url=database_url,
            request=StartInstance(
                command_id=uuid4(),
                definition_key="example.compatible_work",
                definition_version=1,
                instance_input={"subject_id": subject_id},
                route_input={"subject_id": subject_id},
            ),
        )

        request = ClaimWork(
            claim_request_id=uuid4(),
            worker_id="compatible-worker",
            executor_keys=("example.compatible.v1",),
        )
        claim = claim_once(database_url=database_url, request=request)

        assert claim is not None
        assert claim.instance_id == compatible.instance_id
        assert claim.instance_id != older.instance_id
        assert claim_once(database_url=database_url, request=request) == claim
        with pytest.raises(ValueError, match="conflicting input"):
            claim_once(
                database_url=database_url,
                request=replace(request, worker_id="conflicting-worker"),
            )


def test_renewed_draft_attempt_remains_reportable_past_initial_lease_within_hard_bound() -> None:
    with _renewal_postgres_context() as context:
        database_url = context.database_url
        catalog = DefinitionCatalog(database_url=database_url)
        catalog.register(
            _single_step_definition(
                key="example.renewable_draft",
                executor_key="example.renewable_draft.v1",
            )
        )
        subject_id = str(uuid4())
        start_instance(
            database_url=database_url,
            request=StartInstance(
                command_id=uuid4(),
                definition_key="example.renewable_draft",
                definition_version=1,
                instance_input={"subject_id": subject_id},
                route_input={"subject_id": subject_id},
            ),
        )
        worker_id = "renewing-draft-worker"
        claim = claim_once(
            database_url=database_url,
            request=ClaimWork(
                claim_request_id=uuid4(),
                worker_id=worker_id,
                executor_keys=("example.renewable_draft.v1",),
            ),
        )
        assert claim is not None
        renewal_id = uuid4()
        renewal_barrier = Barrier(2)

        def renew_concurrently(_: int) -> object:
            renewal_barrier.wait()
            return renew_once(
                database_url=database_url,
                attempt=claim,
                worker_id=worker_id,
                renewal_id=renewal_id,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_renewal, replayed_renewal = executor.map(renew_concurrently, range(2))
        assert replayed_renewal == first_renewal
        assert (
            KernelInspection(database_url=database_url)
            .snapshot(claim.instance_id)
            .observed_through_sequence
            == 3
        )
        with pytest.raises(ValueError, match="conflicting input"):
            renew_once(
                database_url=database_url,
                attempt=replace(claim, input={"subject_id": str(uuid4())}),
                worker_id=worker_id,
                renewal_id=renewal_id,
            )

        run_input = AgentRunInput(
            configuration=AgentConfiguration(
                "example.renewable_draft",
                1,
                "example.renewable_draft.instructions.v1",
            ),
            task=AgentTask(
                "renewal.draft",
                1,
                AgentRecord(
                    "example.renewable_draft.input",
                    1,
                    (AgentField("subject_id", subject_id),),
                ),
            ),
            thread_id=uuid4(),
            context_through_sequence=0,
            domain_event_context=(),
            audience_context=AgentAudience("workflow_role", "broker"),
            locale="en-CA",
        )

        started = time.monotonic()
        observation = execute_with_renewable_authority(
            executor=FreshAgentExecutor(
                _slow_draft_agent_factory,
                result_class=_SlowDraftCandidate,
                encoder=lambda candidate: {"result": candidate.result},
                timeout_seconds=5,
            ),
            execution=AttemptExecution(
                instance_id=claim.instance_id,
                step_id=claim.step_id,
                attempt_id=claim.attempt_id,
                attempt_number=claim.attempt_number,
                template_key=claim.template_key,
                executor_key=claim.executor_key,
                input=claim.input,
                agent_input=AgentExecutionInput(
                    agent_run_id=uuid4(),
                    attempt_id=claim.attempt_id,
                    run_input=run_input,
                    thread_context=ThreadContext(run_input.thread_id, 0, ()),
                ),
            ),
            cancellation=CancellationToken(),
            renew=lambda: renew_once(
                database_url=database_url,
                attempt=claim,
                worker_id=worker_id,
                renewal_id=uuid4(),
            ),
            lease_seconds=claim.lease_seconds,
        )
        elapsed = time.monotonic() - started
        with psycopg.connect(database_url) as connection, connection.transaction():
            accepted = KernelWork(connection).accept_result(
                claim,
                worker_id=worker_id,
                observation=observation.value,
            )

        assert elapsed > 1
        assert elapsed < 5
        assert accepted.observation == {"result": "drafted"}

        catalog.register(
            _single_step_definition(
                key="example.shutdown_draft",
                executor_key="example.shutdown_draft.v1",
            )
        )
        shutdown_start = start_instance(
            database_url=database_url,
            request=StartInstance(
                command_id=uuid4(),
                definition_key="example.shutdown_draft",
                definition_version=1,
                instance_input={"subject_id": subject_id},
                route_input={"subject_id": subject_id},
            ),
        )
        shutdown_claim = claim_once(
            database_url=database_url,
            request=ClaimWork(
                claim_request_id=uuid4(),
                worker_id=worker_id,
                executor_keys=("example.shutdown_draft.v1",),
            ),
        )
        assert shutdown_claim is not None
        assert shutdown_claim.instance_id == shutdown_start.instance_id
        shutdown = Event()

        def stop_worker() -> None:
            time.sleep(0.2)
            shutdown.set()

        stopper = Thread(target=stop_worker)
        stopper.start()
        with pytest.raises(ExecutionAuthorityLost, match="durable authority"):
            execute_with_renewable_authority(
                executor=FreshAgentExecutor(
                    _slow_draft_agent_factory,
                    result_class=_SlowDraftCandidate,
                    encoder=lambda candidate: {"result": candidate.result},
                    timeout_seconds=5,
                ),
                execution=AttemptExecution(
                    instance_id=shutdown_claim.instance_id,
                    step_id=shutdown_claim.step_id,
                    attempt_id=shutdown_claim.attempt_id,
                    attempt_number=shutdown_claim.attempt_number,
                    template_key=shutdown_claim.template_key,
                    executor_key=shutdown_claim.executor_key,
                    input=shutdown_claim.input,
                    agent_input=AgentExecutionInput(
                        agent_run_id=uuid4(),
                        attempt_id=shutdown_claim.attempt_id,
                        run_input=run_input,
                        thread_context=ThreadContext(run_input.thread_id, 0, ()),
                    ),
                ),
                cancellation=CancellationToken(),
                renew=lambda: renew_once(
                    database_url=database_url,
                    attempt=shutdown_claim,
                    worker_id=worker_id,
                    renewal_id=uuid4(),
                ),
                lease_seconds=shutdown_claim.lease_seconds,
                worker_shutdown=shutdown,
            )
        stopper.join(timeout=1)
        time.sleep(0.8)
        with psycopg.connect(database_url) as connection, connection.transaction():
            abandoned = KernelWork(connection).recover_expired()
        assert abandoned is not None
        assert abandoned.attempt_id == shutdown_claim.attempt_id
        with (
            psycopg.connect(database_url) as connection,
            connection.transaction(),
            pytest.raises(StaleAuthority, match="stale"),
        ):
            KernelWork(connection).accept_result(
                shutdown_claim,
                worker_id=worker_id,
                observation={"result": "late"},
            )


def test_start_command_commits_and_replays_value_identically() -> None:
    with _renewal_postgres_context() as context:
        database_url = context.database_url
        application = context.application
        threads = context.threads
        thread = threads.create(
            CreateThread(
                thread_id=uuid4(),
                channel_kind="email",
                channel_reference="broker-conversation-17",
            )
        )
        command = _renewal_command(
            thread_id=thread.thread_id,
            policy_number="OM-2048",
            policyholder_name="Avery Chen",
            renewal_date="2027-01-31",
            expiring_premium_cents=125_000,
        )

        first = _start_prepared(application, command)
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
        assert snapshot.definition_version == 2
        assert snapshot.state == "open"
        assert [(step.template_key, step.state) for step in snapshot.steps] == [
            ("gather_renewal_facts", "pending")
        ]
        assert snapshot.waits == ()
        assert isinstance(first.result.instance_id, UUID)
        record_renewal_case(
            case_id="transaction.command-atomicity",
            scenario_id="after-commit-response-loss",
            application=application,
            database_url=database_url,
            workflow_id=command.input.workflow_id,
            document={
                "receipt_replayed_value_identically": replay == first,
                "conflicting_reuse_rejected": True,
            },
        )


def test_command_validation_rejects_nested_types_and_semantics_before_commit() -> None:
    with _renewal_postgres_context() as context:
        database_url = context.database_url
        application = context.application
        threads = context.threads
        thread = threads.create(CreateThread(uuid4(), "email", "broker-command-validation"))
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
                policyholder_email="validation@example.test",
                renewal_date="2027-01-31",
                expiring_premium_cents="100000",  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
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
        assert EvidenceInspection(database_url).command_receipts(command_id) == 0
        record_case_observation(
            case_id="transaction.command-atomicity",
            scenario_id="before-commit-validation",
            correlations=Correlations(),
            document={
                "invalid_type_receipts": 0,
                "invalid_semantics_receipts": 0,
            },
        )
        receipt = _start_prepared(application, corrected)

        assert receipt.command_id == command_id


def test_command_handler_fault_rolls_back_application_kernel_event_and_receipt() -> None:
    with _renewal_postgres_context() as context:
        application = context.application
        thread = context.threads.create(
            CreateThread(uuid4(), "email", "broker-command-handler-fault")
        )
        command = _renewal_command(
            thread_id=thread.thread_id,
            policy_number="OM-HANDLER-ROLLBACK",
            policyholder_name="Handler Rollback",
            renewal_date="2027-02-28",
            expiring_premium_cents=101_000,
        )
        _record_command_facts(application, command)
        with psycopg.connect(context.database_url) as connection, connection.transaction():
            connection.execute(
                "CREATE FUNCTION openmagic_runtime.eval_fail_instance_insert() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'synthetic instance fault'; END $$"
            )
            connection.execute(
                "CREATE TRIGGER eval_fail_instance_insert BEFORE INSERT ON "
                "openmagic_runtime.instances FOR EACH ROW EXECUTE FUNCTION "
                "openmagic_runtime.eval_fail_instance_insert()"
            )

        with pytest.raises(psycopg.errors.RaiseException, match="synthetic instance fault"):
            application.start_renewal_outreach(command)
        rolled_back = EvidenceInspection(context.database_url).transaction_state(
            command.command_id,
            command.input.workflow_id,
        )
        with psycopg.connect(context.database_url) as connection, connection.transaction():
            connection.execute(
                "DROP TRIGGER eval_fail_instance_insert ON openmagic_runtime.instances"
            )
            connection.execute("DROP FUNCTION openmagic_runtime.eval_fail_instance_insert()")
        receipt = application.start_renewal_outreach(command)
        committed = EvidenceInspection(context.database_url).transaction_state(
            command.command_id,
            command.input.workflow_id,
        )

        assert rolled_back == type(rolled_back)(0, 0, 0, 0, 0, 0)
        assert committed.command_receipts == 1
        assert committed.workflows == 1
        assert committed.instances == 1
        assert committed.domain_events == 0
        record_renewal_case(
            case_id="transaction.command-atomicity",
            scenario_id="during-handler-rollback",
            application=application,
            database_url=context.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "rolled_back": rolled_back.__dict__,
                "committed": committed.__dict__,
                "instance_id": str(receipt.result.instance_id),
            },
        )


def test_exact_attempt_result_replay_does_not_repeat_route_effects() -> None:
    with _renewal_postgres_context() as context:
        database_url = context.database_url
        application = context.application
        thread = context.threads.create(CreateThread(uuid4(), "email", "broker-result-replay"))
        command = _renewal_command(
            thread_id=thread.thread_id,
            policy_number="OM-REPLAY",
            policyholder_name="Replay Test",
            renewal_date="2027-03-31",
            expiring_premium_cents=100_000,
        )
        started = _start_prepared(application, command)
        attempt = application.claim_workflow_attempt(
            worker_id="workflow-replay",
            claim_request_id=uuid4(),
        )
        assert attempt is not None
        observation = {
            "policy_number": "OM-REPLAY",
            "policyholder_name": "Replay Test",
            "policyholder_email": "policyholder@example.test",
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
    with _renewal_postgres_context() as context:
        database_url = context.database_url
        application = context.application
        threads = context.threads
        thread = threads.create(
            CreateThread(
                thread_id=uuid4(),
                channel_kind="email",
                channel_reference="broker-conversation-18",
            )
        )
        threads.append(
            AppendMessage(
                thread_id=thread.thread_id,
                author_kind="party",
                author_id="broker",
                source_kind="channel",
                source_id=uuid4(),
                content="Use the policyholder's preferred formal greeting.",
            )
        )
        command = _renewal_command(
            thread_id=thread.thread_id,
            policy_number="OM-4096",
            policyholder_name="Morgan Lee",
            renewal_date="2027-02-28",
            expiring_premium_cents=198_500,
        )
        started = _start_prepared(application, command)

        first_attempt = application.run_workflow_worker_once(worker_id="workflow-a")
        after_facts = KernelInspection(database_url=database_url).snapshot(
            started.result.instance_id
        )
        second_attempt = application.run_workflow_worker_once(worker_id="workflow-a")
        after_draft = KernelInspection(database_url=database_url).snapshot(
            started.result.instance_id
        )
        with psycopg.connect(database_url) as connection:
            agent_evidence = connection.execute(
                "SELECT d.body, r.input FROM example_insurance.renewal_drafts AS d "
                "JOIN openmagic_runtime.agent_runs AS r ON r.agent_run_id = d.agent_run_id "
                "WHERE d.workflow_id = %s",
                (command.input.workflow_id,),
            ).fetchone()

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
        assert agent_evidence is not None
        assert "preferred formal greeting" in str(agent_evidence[0])
        assert agent_evidence[1]["configuration"] == {
            "agent_key": "example_insurance.renewal_draft",
            "agent_version": 1,
            "instruction_key": "example_insurance.renewal_draft.en_ca.v1",
        }
        assert agent_evidence[1]["context_through_sequence"] == 1


def test_gather_facts_rejects_stale_command_assertions_against_durable_business_state() -> None:
    with _renewal_postgres_context() as context:
        database_url = context.database_url
        application = context.application
        thread = context.threads.create(
            CreateThread(uuid4(), "email", "broker-stale-renewal-facts")
        )
        command = _renewal_command(
            thread_id=thread.thread_id,
            policy_number="OM-STALE-FACTS",
            policyholder_name="Durable State",
            renewal_date="2027-08-31",
            expiring_premium_cents=250_000,
        )
        _start_prepared(application, command)
        application.replace_renewal_facts(
            RenewalFacts(
                policy_id=command.input.policy_id,
                policy_number=command.input.policy_number,
                policyholder_name=command.input.policyholder_name,
                policyholder_email=command.input.policyholder_email,
                renewal_date=command.input.renewal_date,
                expiring_premium_cents=275_000,
            )
        )

        with pytest.raises(StaleRenewalFacts, match="changed"):
            application.run_workflow_worker_once(worker_id="stale-facts-worker")

        snapshot = KernelInspection(database_url=database_url).snapshot(
            application.start_renewal_outreach(command).result.instance_id
        )
        assert [(step.template_key, step.state) for step in snapshot.steps] == [
            ("gather_renewal_facts", "pending")
        ]


def test_agent_attempt_replay_uses_one_durable_run_without_reexecution() -> None:
    with _renewal_postgres_context() as context:
        application = context.application
        thread = context.threads.create(CreateThread(uuid4(), "email", "broker-agent-replay"))
        command = _renewal_command(
            thread_id=thread.thread_id,
            policy_number="OM-AGENT-REPLAY",
            policyholder_name="Agent Replay",
            renewal_date="2027-05-31",
            expiring_premium_cents=300_000,
        )
        _start_prepared(application, command)
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
    with _renewal_postgres_context() as context:
        database_url = context.database_url
        command_id = uuid4()
        request = StartInstance(
            command_id=command_id,
            definition_key="example_insurance.renewal_outreach",
            definition_version=2,
            instance_input={
                "workflow_id": str(uuid4()),
                "thread_id": str(uuid4()),
                "policy_id": str(uuid4()),
            },
            route_input={
                "policy_id": str(uuid4()),
                "policy_number": "OM-ROUTE-1",
                "policyholder_name": "Route Replay",
                "policyholder_email": "route-replay@example.test",
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
        trace_event_ids, _ = EvidenceInspection(database_url).renewal_demo_ids(first.instance_id)
        record_case_observation(
            case_id="route.finite-materialization",
            scenario_id="complete-batch-replay",
            correlations=Correlations(
                command_ids=(command_id,),
                instance_ids=(first.instance_id,),
                step_ids=tuple(step.step_id for step in snapshot.steps),
                trace_event_ids=trace_event_ids,
            ),
            document={
                "replayed_value_identically": replay == first,
                "materialized_steps": len(first.steps),
                "materialized_waits": len(first.waits),
            },
        )


@pytest.mark.parametrize(
    ("table", "scenario_id"),
    [
        ("steps", "step-boundary-rollback"),
        ("trace_events", "trace-boundary-rollback"),
    ],
)
def test_start_route_fault_rolls_back_the_whole_occurrence_batch(
    table: str,
    scenario_id: str,
) -> None:
    with _renewal_postgres_context() as context:
        command_id = uuid4()
        workflow_id = uuid4()
        request = StartInstance(
            command_id=command_id,
            definition_key="example_insurance.renewal_outreach",
            definition_version=2,
            instance_input={
                "workflow_id": str(workflow_id),
                "thread_id": str(uuid4()),
                "policy_id": str(uuid4()),
            },
            route_input={
                "policy_id": str(uuid4()),
                "policy_number": "OM-ROUTE-FAULT",
                "policyholder_name": "Route Fault",
                "policyholder_email": "route-fault@example.test",
                "renewal_date": "2027-06-30",
                "expiring_premium_cents": 100_000,
            },
        )
        function_name = f"eval_fail_{table}_insert"
        with psycopg.connect(context.database_url) as connection, connection.transaction():
            connection.execute(
                sql.SQL(
                    "CREATE FUNCTION openmagic_runtime.{}() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                    "'synthetic route fault'; END $$"
                ).format(sql.Identifier(function_name))
            )
            connection.execute(
                sql.SQL(
                    "CREATE TRIGGER eval_route_fault BEFORE INSERT ON "
                    "openmagic_runtime.{} FOR EACH ROW EXECUTE FUNCTION "
                    "openmagic_runtime.{}()"
                ).format(
                    sql.Identifier(table),
                    sql.Identifier(function_name),
                )
            )
        with pytest.raises(psycopg.errors.RaiseException, match="synthetic route fault"):
            start_instance(database_url=context.database_url, request=request)
        state = EvidenceInspection(context.database_url).transaction_state(
            command_id,
            workflow_id,
        )
        with psycopg.connect(context.database_url) as connection, connection.transaction():
            connection.execute(
                sql.SQL("DROP TRIGGER eval_route_fault ON openmagic_runtime.{}").format(
                    sql.Identifier(table)
                )
            )
            connection.execute(
                sql.SQL("DROP FUNCTION openmagic_runtime.{}()").format(
                    sql.Identifier(function_name)
                )
            )

        assert state.instances == 0
        assert state.steps == 0
        assert state.trace_events == 0
        record_case_observation(
            case_id="route.finite-materialization",
            scenario_id=scenario_id,
            correlations=Correlations(),
            document={
                "fault_table": table,
                "instances": state.instances,
                "steps": state.steps,
                "trace_events": state.trace_events,
            },
        )


def test_competing_command_and_step_claims_preserve_cardinality_one() -> None:
    with _renewal_postgres_context() as context:
        application = context.application
        thread = context.threads.create(
            CreateThread(uuid4(), "email", "broker-conversation-command-race")
        )
        command = _renewal_command(
            thread_id=thread.thread_id,
            policy_number="OM-RACE-1",
            policyholder_name="Casey Nguyen",
            renewal_date="2027-07-31",
            expiring_premium_cents=512_000,
        )
        _record_command_facts(application, command)
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
    with _renewal_postgres_context() as context:
        database_url = context.database_url
        application = context.application
        threads = context.threads
        intended = threads.create(CreateThread(uuid4(), "email", "broker-stale-intended"))
        wrong = threads.create(CreateThread(uuid4(), "email", "broker-stale-wrong"))
        command = _renewal_command(
            thread_id=intended.thread_id,
            policy_number="OM-FENCE-1",
            policyholder_name="Jordan Ali",
            renewal_date="2027-08-31",
            expiring_premium_cents=618_000,
        )
        _start_prepared(application, command)
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
        expired_delivery = claim_delivery_once(
            database_url=database_url,
            request=ClaimDelivery(uuid4(), "delivery-worker"),
        )
        assert expired_delivery is not None
        time.sleep(1.1)
        delivery = claim_delivery_once(
            database_url=database_url,
            request=ClaimDelivery(uuid4(), "replacement-delivery-worker"),
        )
        assert delivery is not None

        with pytest.raises(StaleDeliveryAuthority, match="stale"):
            acknowledge_delivery(
                database_url=database_url,
                claim=expired_delivery,
                worker_id="delivery-worker",
                proposed_thread_id=intended.thread_id,
            )
        with pytest.raises(RuntimeError, match="wrong exact Thread"):
            acknowledge_delivery(
                database_url=database_url,
                claim=delivery,
                worker_id="replacement-delivery-worker",
                proposed_thread_id=wrong.thread_id,
            )
        acknowledgement = acknowledge_delivery(
            database_url=database_url,
            claim=delivery,
            worker_id="replacement-delivery-worker",
            proposed_thread_id=intended.thread_id,
        )

        assert acknowledgement.thread_id == intended.thread_id
        assert threads.read(wrong.thread_id).messages == ()
        record_renewal_case(
            case_id="delivery.exact-thread",
            scenario_id="crash-before-append",
            application=application,
            database_url=database_url,
            workflow_id=command.input.workflow_id,
            document={
                "expired_attempt_rejected": True,
                "replacement_thread_id": str(acknowledgement.thread_id),
            },
            worker_ids=("delivery-worker", "replacement-delivery-worker"),
        )
        record_renewal_case(
            case_id="delivery.exact-thread",
            scenario_id="wrong-thread",
            application=application,
            database_url=database_url,
            workflow_id=command.input.workflow_id,
            document={
                "wrong_thread_rejected": True,
                "wrong_thread_message_count": len(threads.read(wrong.thread_id).messages),
            },
        )


def test_one_domain_event_can_create_multiple_exact_destination_delivery_obligations() -> None:
    with _renewal_postgres_context() as context:
        database_url = context.database_url
        threads = context.threads
        first = threads.create(CreateThread(uuid4(), "email", "multi-delivery-first"))
        second = threads.create(CreateThread(uuid4(), "email", "multi-delivery-second"))
        domain_event_id = uuid4()
        retry_policy = DeliveryRetryPolicy(
            version=1,
            max_attempts=2,
            delays_seconds=(0,),
            lease_seconds=1,
            retryable_failure_classes=("transient",),
            terminal_failure_classes=("permanent",),
        )
        with psycopg.connect(database_url) as connection, connection.transaction():
            control = DeliveryControl(connection)
            first_intent = control.create(
                domain_event_id=domain_event_id,
                thread_id=first.thread_id,
                audience={"kind": "party", "identifier": "first"},
                message_author={"kind": "system", "identifier": "renewal"},
                content_descriptor={"template": "renewal.first.v1"},
                message_content="First exact destination",
                retry_policy=retry_policy,
            )
            second_intent = control.create(
                domain_event_id=domain_event_id,
                thread_id=second.thread_id,
                audience={"kind": "party", "identifier": "second"},
                message_author={"kind": "system", "identifier": "renewal"},
                content_descriptor={"template": "renewal.second.v1"},
                message_content="Second exact destination",
                retry_policy=retry_policy,
            )
            projected = RuntimeEvidenceReader(connection).deliveries(domain_event_id)

        assert first_intent.thread_id == first.thread_id
        assert second_intent.thread_id == second.thread_id
        assert {item.delivery_id for item in projected} == {
            first_intent.delivery_id,
            second_intent.delivery_id,
        }


@pytest.mark.integration
def test_seeded_step_and_delivery_claim_races_hold_cardinality_one_100_times() -> None:
    seeds = tuple(range(100))
    with _renewal_postgres_context() as context:
        application = context.application
        thread = context.threads.create(CreateThread(uuid4(), "email", "broker-cardinality-races"))
        with ThreadPoolExecutor(max_workers=2) as executor:
            for seed in seeds:
                command = _renewal_command(
                    thread_id=thread.thread_id,
                    policy_number=f"OM-RACE-{seed}",
                    policyholder_name=f"Seed {seed}",
                    renewal_date="2027-10-31",
                    expiring_premium_cents=800_000 + seed,
                )
                _start_prepared(application, command)
                step_barrier = Barrier(2)

                def claim_step(
                    index: int,
                    barrier: Barrier = step_barrier,
                    race_seed: int = seed,
                ) -> ClaimedAttempt | None:
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
                ) -> ClaimedDelivery | None:
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
    with _renewal_postgres_context() as context:
        application = context.application
        threads = context.threads
        intended = threads.create(CreateThread(uuid4(), "email", "broker-conversation-intended"))
        other = threads.create(CreateThread(uuid4(), "email", "broker-conversation-other"))
        command = _renewal_command(
            thread_id=intended.thread_id,
            policy_number="OM-8192",
            policyholder_name="Taylor Singh",
            renewal_date="2027-03-31",
            expiring_premium_cents=211_000,
        )
        _start_prepared(application, command)
        application.run_workflow_worker_once(worker_id="workflow-a")
        application.run_workflow_worker_once(worker_id="workflow-a")

        claim_barrier = Barrier(2)

        def claim_delivery(worker_id: str) -> ClaimedDelivery | None:
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
        start_replay = application.start_renewal_outreach(command)
        evidence = json.loads(application.renewal_evidence_json(command.input.workflow_id))

        assert replay == acknowledgement
        assert acknowledgement.thread_id == intended.thread_id
        assert acknowledgement.message_sequence == 1
        assert len(intended_thread.messages) == 1
        assert intended_thread.messages[0].message_id == acknowledgement.message_id
        assert "OM-8192" in intended_thread.messages[0].content
        assert "forged" not in intended_thread.messages[0].content
        assert other_thread.messages == ()
        assert len(evidence["correlations"]["delivery_ids"]) == 1
        record_renewal_case(
            case_id="delivery.exact-thread",
            scenario_id="duplicate-creation",
            application=application,
            database_url=context.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "start_replay_command_id": str(start_replay.command_id),
                "delivery_count": len(evidence["correlations"]["delivery_ids"]),
            },
        )
        record_renewal_case(
            case_id="delivery.exact-thread",
            scenario_id="competing-claim",
            application=application,
            database_url=context.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "claim_winners": len(winners),
                "exact_thread": acknowledgement.thread_id == intended.thread_id,
                "other_thread_messages": len(other_thread.messages),
            },
            worker_ids=("delivery-a", "delivery-b"),
        )
        record_renewal_case(
            case_id="acknowledgement.atomic-append",
            scenario_id="atomic-append",
            application=application,
            database_url=context.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "acknowledgement_replayed": replay == acknowledgement,
                "message_count": len(intended_thread.messages),
            },
        )


def test_fresh_worker_processes_recover_the_complete_sanitized_evidence_chain(tmp_path) -> None:
    with TestDeployment(working_directory=tmp_path) as deployment:
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        context = _prepared_renewal_context(deployment.database_url)
        application = context.application
        thread = context.threads.create(
            CreateThread(uuid4(), "email", "broker-conversation-process-recovery")
        )
        command = _renewal_command(
            thread_id=thread.thread_id,
            policy_number="OM-16384",
            policyholder_name="Jamie Patel",
            renewal_date="2027-04-30",
            expiring_premium_cents=305_500,
        )
        _start_prepared(application, command)

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
            "delivery_states",
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
            "agent_run_ids",
            "approval_grant_ids",
            "attempt_ids",
            "command_id",
            "decision_ids",
            "delivery_ids",
            "domain_event_ids",
            "draft_agent_run_ids",
            "effect_evidence_ids",
            "instance_id",
            "logical_effect_ids",
            "message_ids",
            "signal_ids",
            "step_ids",
            "thread_id",
            "workflow_id",
        }


def test_process_loss_after_claim_is_recovered_and_fenced_by_a_fresh_process(tmp_path) -> None:
    with TestDeployment(working_directory=tmp_path) as deployment:
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        context = _prepared_renewal_context(deployment.database_url)
        application = context.application
        thread = context.threads.create(
            CreateThread(uuid4(), "email", "broker-conversation-claim-loss")
        )
        command = _renewal_command(
            thread_id=thread.thread_id,
            policy_number="OM-32768",
            policyholder_name="Riley Brooks",
            renewal_date="2027-05-31",
            expiring_premium_cents=411_000,
        )
        _start_prepared(application, command)

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
        record_renewal_case(
            case_id="recovery.fresh-process",
            scenario_id="after-claim-loss",
            application=application,
            database_url=deployment.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "lost_process_id": lost_process.pid,
                "recovery_process_id": recovery_process.pid,
                "attempt_states": evidence["outcomes"]["attempt_states"],
            },
            process_ids=(lost_process.pid, recovery_process.pid),
        )


def test_agent_process_loss_terminalizes_run_and_retries_without_phantom_authority(
    tmp_path,
) -> None:
    with TestDeployment(working_directory=tmp_path) as deployment:
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        context = _prepared_renewal_context(deployment.database_url)
        application = context.application
        thread = context.threads.create(CreateThread(uuid4(), "email", "broker-agent-process-loss"))
        command = _renewal_command(
            thread_id=thread.thread_id,
            policy_number="OM-AGENT-LOSS",
            policyholder_name="Agent Loss",
            renewal_date="2027-11-30",
            expiring_premium_cents=910_000,
        )
        _start_prepared(application, command)
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
        record_renewal_case(
            case_id="executor.typed-malformed-timeout",
            scenario_id="late",
            application=application,
            database_url=deployment.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "lost_process_id": lost_process.pid,
                "recovery_process_id": recovery_process.pid,
                "agent_run_states": evidence["outcomes"]["agent_run_states"],
            },
            process_ids=(lost_process.pid, recovery_process.pid),
        )


def test_delivery_process_loss_after_claim_recovers_without_duplicate_message(tmp_path) -> None:
    with TestDeployment(working_directory=tmp_path) as deployment:
        deployment.terminate_role("workflow-worker")
        deployment.terminate_role("delivery-worker")
        context = _prepared_renewal_context(deployment.database_url)
        application = context.application
        threads = context.threads
        thread = threads.create(CreateThread(uuid4(), "email", "broker-delivery-claim-loss"))
        command = _renewal_command(
            thread_id=thread.thread_id,
            policy_number="OM-65536",
            policyholder_name="Quinn Martin",
            renewal_date="2027-09-30",
            expiring_premium_cents=720_000,
        )
        _start_prepared(application, command)
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
            "delivery_states",
            "delivered",
        )

        assert recovery_process.pid != lost_process.pid
        assert evidence["outcomes"]["delivery_attempt_states"] == [["abandoned", "succeeded"]]
        assert len(threads.read(thread.thread_id).messages) == 1
        record_renewal_case(
            case_id="delivery.exact-thread",
            scenario_id="crash-after-append",
            application=application,
            database_url=deployment.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "lost_process_id": lost_process.pid,
                "recovery_process_id": recovery_process.pid,
                "message_count": len(threads.read(thread.thread_id).messages),
            },
            process_ids=(lost_process.pid, recovery_process.pid),
        )
        record_renewal_case(
            case_id="acknowledgement.atomic-append",
            scenario_id="post-append-loss-recovery",
            application=application,
            database_url=deployment.database_url,
            workflow_id=command.input.workflow_id,
            document={
                "attempt_states": evidence["outcomes"]["delivery_attempt_states"],
                "message_count": len(threads.read(thread.thread_id).messages),
            },
            process_ids=(lost_process.pid, recovery_process.pid),
        )
        assert evidence["invariant_violations"] == []


def _wait_for_outcome(
    application: ExampleInsurance,
    workflow_id: UUID,
    key: str,
    expected: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        evidence = json.loads(application.renewal_evidence_json(workflow_id))
        outcomes = evidence["outcomes"]
        actual = outcomes[key]
        if actual == expected or (isinstance(actual, list) and expected in actual):
            return evidence
        time.sleep(0.05)
    raise AssertionError(f"renewal evidence did not reach {key}={expected}")


def _wait_for_attempt_state(
    application: ExampleInsurance,
    workflow_id: UUID,
    expected: str,
) -> dict[str, Any]:
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
) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        evidence = json.loads(application.renewal_evidence_json(workflow_id))
        attempts = evidence["outcomes"]["delivery_attempt_states"]
        if any(expected in states for states in attempts):
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
