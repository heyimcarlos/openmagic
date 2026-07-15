from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
from example_insurance.migrations import apply_migrations
from openmagic_evals.harness._postgres import postgres_container
from openmagic_runtime.kernel.control import (
    AcceptSignal,
    KernelControl,
    StartInstance,
    start_instance,
)
from openmagic_runtime.kernel.definitions import (
    DefinitionCatalog,
    DefinitionIdentity,
    FieldBinding,
    FieldContract,
    RetryPolicy,
    Route,
    RouteOutput,
    StepTemplate,
    WaitTemplate,
    WorkflowDefinition,
)
from openmagic_runtime.kernel.inspection import KernelInspection


def signal_race_definition() -> WorkflowDefinition:
    subject = (FieldContract("subject_id", "uuid"),)
    return WorkflowDefinition(
        identity=DefinitionIdentity("eval.signal_race", 1),
        instance_input_contract=subject,
        step_templates=(
            StepTemplate(
                key="winner",
                executor_key="eval.signal_winner.v1",
                input_contract=subject,
                observation_contract=(FieldContract("result", "string"),),
                output_contract=(FieldContract("result", "string"),),
                lease_seconds=1,
                maximum_attempt_seconds=2,
                retry_policy=RetryPolicy(()),
            ),
        ),
        wait_templates=(
            WaitTemplate(
                key="decision",
                signal_type="eval.signal.decision",
                input_contract=subject,
            ),
        ),
        routes=(
            Route(
                key="start",
                activation="start",
                activation_contract=subject,
                outputs=(
                    RouteOutput(
                        slot="decision",
                        kind="wait",
                        template_key="decision",
                        input_bindings=(FieldBinding("subject_id", "subject_id"),),
                    ),
                ),
            ),
            Route(
                key="approve",
                activation="signal",
                activation_contract=subject,
                outputs=(
                    RouteOutput(
                        slot="approved",
                        kind="step",
                        template_key="winner",
                        input_bindings=(FieldBinding("subject_id", "subject_id"),),
                    ),
                ),
            ),
            Route(
                key="revise",
                activation="signal",
                activation_contract=subject,
                outputs=(
                    RouteOutput(
                        slot="revision",
                        kind="step",
                        template_key="winner",
                        input_bindings=(FieldBinding("subject_id", "subject_id"),),
                    ),
                ),
            ),
        ),
    )


def test_competing_signals_have_one_winner_in_100_seeded_real_transaction_races() -> None:
    with postgres_container(database_name=f"openmagic_test_{uuid4().hex}") as postgres:
        database_url = postgres.get_connection_url(driver=None)
        apply_migrations(database_url)
        DefinitionCatalog(database_url=database_url).register(signal_race_definition())
        for seed in range(100):
            subject_id = uuid4()
            started = start_instance(
                database_url=database_url,
                request=StartInstance(
                    command_id=uuid4(),
                    definition_key="eval.signal_race",
                    definition_version=1,
                    instance_input={"subject_id": str(subject_id)},
                    route_input={"subject_id": str(subject_id)},
                ),
            )
            wait_id = started.waits["decision"]
            barrier = Barrier(2)
            jitter = random.Random(seed)
            requests = (
                (
                    jitter.random() / 1000,
                    AcceptSignal(
                        uuid4(),
                        started.instance_id,
                        wait_id,
                        "eval.signal.decision",
                        1,
                        {"subject_id": str(subject_id)},
                        "approve",
                    ),
                ),
                (
                    jitter.random() / 1000,
                    AcceptSignal(
                        uuid4(),
                        started.instance_id,
                        wait_id,
                        "eval.signal.decision",
                        1,
                        {"subject_id": str(subject_id)},
                        "revise",
                    ),
                ),
            )

            def submit(
                delay: float,
                request: AcceptSignal,
                race_barrier: Barrier,
            ) -> object:
                race_barrier.wait()
                time.sleep(delay)
                try:
                    with psycopg.connect(database_url) as connection, connection.transaction():
                        return KernelControl(connection).accept_signal(request)
                except RuntimeError as error:
                    return error

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = tuple(
                    executor.submit(submit, delay, request, barrier) for delay, request in requests
                )
                outcomes = tuple(future.result() for future in futures)
            snapshot = KernelInspection(database_url=database_url).snapshot(started.instance_id)

            assert sum(not isinstance(outcome, RuntimeError) for outcome in outcomes) == 1, seed
            assert sum(isinstance(outcome, RuntimeError) for outcome in outcomes) == 1, seed
            assert len(snapshot.steps) == 1, seed
            assert snapshot.waits[0].state == "satisfied", seed
