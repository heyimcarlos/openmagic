"""Recorded Signal, Attempt-result, and Route-activation race corpora."""

from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

import psycopg
from openmagic_runtime.kernel.control import (
    AcceptSignal,
    KernelControl,
    SignalReceipt,
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
from openmagic_runtime.kernel.work import ClaimWork, DispositionRequired, claim_once

from openmagic_evals.evidence.contracts import Correlations
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.evidence.race_models import (
    RaceCorpus,
    RaceSeedResult,
    jitter_pair,
    race_observation,
)
from openmagic_evals.evidence.race_processes import ProcessRaceResult, run_process_contenders


def _transition_definition() -> WorkflowDefinition:
    fields = (FieldContract("value", "string"),)
    return WorkflowDefinition(
        identity=DefinitionIdentity("eval.issue71_transition_race", 1),
        instance_input_contract=fields,
        step_templates=(
            StepTemplate(
                key="origin",
                executor_key="eval.issue71_origin.v1",
                input_contract=fields,
                observation_contract=fields,
                output_contract=fields,
                lease_seconds=2,
                maximum_attempt_seconds=5,
                retry_policy=RetryPolicy(()),
            ),
            StepTemplate(
                key="finish",
                executor_key="eval.issue71_finish.v1",
                input_contract=fields,
                observation_contract=fields,
                output_contract=fields,
                lease_seconds=2,
                maximum_attempt_seconds=5,
                retry_policy=RetryPolicy(()),
            ),
        ),
        wait_templates=(),
        routes=(
            Route(
                key="start",
                activation="start",
                activation_contract=fields,
                outputs=(
                    RouteOutput(
                        slot="origin",
                        kind="step",
                        template_key="origin",
                        input_bindings=(FieldBinding("value", "value"),),
                    ),
                ),
            ),
            Route(
                key="finish_after_origin",
                activation="step",
                activation_contract=fields,
                source_template_key="origin",
                outputs=(
                    RouteOutput(
                        slot="finish",
                        kind="step",
                        template_key="finish",
                        input_bindings=(FieldBinding("value", "value"),),
                    ),
                ),
            ),
        ),
    )


def _signal_definition() -> WorkflowDefinition:
    fields = (FieldContract("value", "string"),)
    return WorkflowDefinition(
        identity=DefinitionIdentity("eval.issue71_signal_race", 1),
        instance_input_contract=fields,
        step_templates=(
            StepTemplate(
                key="winner",
                executor_key="eval.issue71_signal_winner.v1",
                input_contract=fields,
                observation_contract=fields,
                output_contract=fields,
                lease_seconds=2,
                maximum_attempt_seconds=5,
                retry_policy=RetryPolicy(()),
            ),
        ),
        wait_templates=(
            WaitTemplate(
                key="decision",
                signal_type="eval.issue71.decision",
                input_contract=fields,
            ),
        ),
        routes=(
            Route(
                key="start",
                activation="start",
                activation_contract=fields,
                outputs=(
                    RouteOutput(
                        slot="decision",
                        kind="wait",
                        template_key="decision",
                        input_bindings=(FieldBinding("value", "value"),),
                    ),
                ),
            ),
            Route(
                key="approve",
                activation="signal",
                activation_contract=fields,
                outputs=(
                    RouteOutput(
                        slot="approved",
                        kind="step",
                        template_key="winner",
                        input_bindings=(FieldBinding("value", "value"),),
                    ),
                ),
            ),
            Route(
                key="revise",
                activation="signal",
                activation_contract=fields,
                outputs=(
                    RouteOutput(
                        slot="revision",
                        kind="step",
                        template_key="winner",
                        input_bindings=(FieldBinding("value", "value"),),
                    ),
                ),
            ),
        ),
    )


def transition_race_definitions() -> tuple[WorkflowDefinition, WorkflowDefinition]:
    return _transition_definition(), _signal_definition()


def run_transition_races(
    database_url: str,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> tuple[RaceCorpus, RaceCorpus, RaceCorpus]:
    catalog = DefinitionCatalog(database_url=database_url)
    for definition in transition_race_definitions():
        catalog.register(definition)
    inspection = EvidenceInspection(database_url)
    signal_results: list[RaceSeedResult] = []
    attempt_results: list[RaceSeedResult] = []
    route_results: list[RaceSeedResult] = []
    for seed in seeds:
        signal_results.append(_signal_trial(database_url, inspection, seed))
        attempt, route = _attempt_and_route_trial(database_url, inspection, seed)
        attempt_results.append(attempt)
        route_results.append(route)
    return (
        RaceCorpus(
            case_id="race.wait-signal",
            uses_overlap_barrier=True,
            varied_jitter=True,
            database_constraint="openmagic_runtime.signals(wait_id)",
            expected_public_outcomes=("accepted", "conflict"),
            results=tuple(signal_results),
        ),
        RaceCorpus(
            case_id="race.attempt-result",
            uses_overlap_barrier=True,
            varied_jitter=True,
            database_constraint="one accepted result per Attempt",
            expected_public_outcomes=("accepted", "replayed"),
            results=tuple(attempt_results),
        ),
        RaceCorpus(
            case_id="race.route-activation",
            uses_overlap_barrier=True,
            varied_jitter=True,
            database_constraint="one materialized output per Route slot",
            expected_public_outcomes=(
                "value_identical_receipt",
                "value_identical_receipt",
            ),
            results=tuple(route_results),
        ),
    )


def _signal_trial(
    database_url: str,
    inspection: EvidenceInspection,
    seed: int,
) -> RaceSeedResult:
    started = start_instance(
        database_url=database_url,
        request=StartInstance(
            command_id=uuid4(),
            definition_key="eval.issue71_signal_race",
            definition_version=1,
            instance_input={"value": f"signal-{seed}"},
            route_input={"value": f"signal-{seed}"},
        ),
    )
    wait_id = started.waits["decision"]
    requests = (
        AcceptSignal(
            uuid4(),
            started.instance_id,
            wait_id,
            "eval.issue71.decision",
            1,
            {"value": f"signal-{seed}"},
            "approve",
        ),
        AcceptSignal(
            uuid4(),
            started.instance_id,
            wait_id,
            "eval.issue71.decision",
            1,
            {"value": f"signal-{seed}"},
            "revise",
        ),
    )
    jitters = jitter_pair(seed, 400_000)
    contenders = run_process_contenders(
        database_url,
        case_id="race.wait-signal",
        seed=seed,
        jitter_microseconds=jitters,
        operation="accept_signal",
        payloads=requests,
    )
    outcomes: tuple[ProcessRaceResult, ProcessRaceResult] = contenders.results
    public = tuple("accepted" if item.error_type is None else "conflict" for item in outcomes)
    unexpected_errors = tuple(
        item.error_type for item in outcomes if item.error_type not in {None, "RuntimeError"}
    )
    if unexpected_errors:
        raise RuntimeError(f"Signal race contender failed: {unexpected_errors}")
    winner = next(item.require_value() for item in outcomes if item.error_type is None)
    if not isinstance(winner, SignalReceipt):
        raise AssertionError("Signal race did not return its typed winner receipt")
    constraint_rows = inspection.accepted_signals(wait_id)
    if public.count("accepted") != 1 or constraint_rows != 1:
        raise AssertionError(f"Signal constraint disagreed for seed {seed}")
    document = race_observation(
        {
            "seed": seed,
            "jitter_microseconds": jitters,
            "public_outcomes": public,
            "constraint_rows": constraint_rows,
            "signal_id": str(winner.signal_id),
        }
    )
    return RaceSeedResult(
        seed=seed,
        jitter_microseconds=jitters,
        public_outcomes=public,
        constraint_rows=constraint_rows,
        correlations=Correlations(
            instance_ids=(started.instance_id,),
            step_ids=tuple(winner.steps.values()),
            wait_ids=(wait_id,),
            signal_ids=(winner.signal_id,),
            trace_event_ids=(started.trace_event_id, winner.trace_event_id),
            process_ids=contenders.process_ids,
        ),
        observation=document,
        contender_process_ids=contenders.process_ids,
        overlap_barrier_observed=contenders.overlap_barrier_observed,
    )


def _attempt_and_route_trial(
    database_url: str,
    inspection: EvidenceInspection,
    seed: int,
) -> tuple[RaceSeedResult, RaceSeedResult]:
    started = start_instance(
        database_url=database_url,
        request=StartInstance(
            command_id=uuid4(),
            definition_key="eval.issue71_transition_race",
            definition_version=1,
            instance_input={"value": f"transition-{seed}"},
            route_input={"value": f"transition-{seed}"},
        ),
    )
    claim = claim_once(
        database_url=database_url,
        request=ClaimWork(uuid4(), f"attempt-result-{seed}", ("eval.issue71_origin.v1",)),
    )
    if claim is None:
        raise AssertionError("Attempt-result race setup did not claim its origin Step")
    observation = {"value": f"transition-{seed}"}
    attempt_jitters = jitter_pair(seed, 500_000)
    attempt_contenders = run_process_contenders(
        database_url,
        case_id="race.attempt-result",
        seed=seed,
        jitter_microseconds=attempt_jitters,
        operation="attempt_result",
        payloads=(
            (claim, f"attempt-result-{seed}", observation),
            (claim, f"attempt-result-{seed}", observation),
        ),
    )
    dispositions = cast(
        tuple[DispositionRequired, DispositionRequired],
        tuple(result.require_value() for result in attempt_contenders.results),
    )
    attempt_public = tuple("replayed" if item.replayed else "accepted" for item in dispositions)
    attempt_count = inspection.completed_attempts(claim.attempt_id)
    if sorted(attempt_public) != ["accepted", "replayed"] or attempt_count != 1:
        raise AssertionError(f"Attempt-result constraint disagreed for seed {seed}")
    attempt_document = race_observation(
        {
            "seed": seed,
            "jitter_microseconds": attempt_jitters,
            "public_outcomes": attempt_public,
            "constraint_rows": attempt_count,
            "attempt_id": str(claim.attempt_id),
        }
    )
    attempt_result = RaceSeedResult(
        seed=seed,
        jitter_microseconds=attempt_jitters,
        public_outcomes=attempt_public,
        constraint_rows=attempt_count,
        correlations=Correlations(
            instance_ids=(claim.instance_id,),
            step_ids=(claim.step_id,),
            attempt_ids=(claim.attempt_id,),
            trace_event_ids=(started.trace_event_id,),
            worker_ids=(f"attempt-result-{seed}",),
            process_ids=attempt_contenders.process_ids,
        ),
        observation=attempt_document,
        contender_process_ids=attempt_contenders.process_ids,
        overlap_barrier_observed=attempt_contenders.overlap_barrier_observed,
    )
    with psycopg.connect(database_url) as connection, connection.transaction():
        KernelControl(connection).succeed(
            next(item for item in dispositions if not item.replayed),
            output=observation,
            outcome_route="finish_after_origin",
            route_input=observation,
        )

    route_started = start_instance(
        database_url=database_url,
        request=StartInstance(
            command_id=uuid4(),
            definition_key="eval.issue71_transition_race",
            definition_version=1,
            instance_input={"value": f"route-{seed}"},
            route_input={"value": f"route-{seed}"},
        ),
    )
    route_claim = claim_once(
        database_url=database_url,
        request=ClaimWork(uuid4(), f"route-result-{seed}", ("eval.issue71_origin.v1",)),
    )
    if route_claim is None:
        raise AssertionError("Route race setup did not claim its origin Step")
    route_observation = {"value": f"route-{seed}"}
    route_jitters = jitter_pair(seed, 600_000)
    route_contenders = run_process_contenders(
        database_url,
        case_id="race.route-activation",
        seed=seed,
        jitter_microseconds=route_jitters,
        operation="route_activation",
        payloads=(
            (route_claim, f"route-result-{seed}", route_observation),
            (route_claim, f"route-result-{seed}", route_observation),
        ),
    )
    route_outcomes = cast(
        tuple[
            tuple[dict[str, UUID], dict[str, UUID], bool],
            tuple[dict[str, UUID], dict[str, UUID], bool],
        ],
        tuple(result.require_value() for result in route_contenders.results),
    )
    route_count = inspection.materialized_steps(route_started.instance_id, "finish")
    route_public = ("value_identical_receipt", "value_identical_receipt")
    if (
        route_count != 1
        or route_outcomes[0][:2] != route_outcomes[1][:2]
        or sorted(item[2] for item in route_outcomes) != [False, True]
    ):
        raise AssertionError(f"Route activation constraint disagreed for seed {seed}")
    finish_step_ids = tuple(route_outcomes[0][0].values())
    route_document = race_observation(
        {
            "seed": seed,
            "jitter_microseconds": route_jitters,
            "public_outcomes": route_public,
            "constraint_rows": route_count,
            "step_ids": [str(step_id) for step_id in finish_step_ids],
        }
    )
    route_result = RaceSeedResult(
        seed=seed,
        jitter_microseconds=route_jitters,
        public_outcomes=route_public,
        constraint_rows=route_count,
        correlations=Correlations(
            instance_ids=(route_started.instance_id,),
            step_ids=(route_claim.step_id, *finish_step_ids),
            attempt_ids=(route_claim.attempt_id,),
            process_ids=route_contenders.process_ids,
        ),
        observation=route_document,
        contender_process_ids=route_contenders.process_ids,
        overlap_barrier_observed=route_contenders.overlap_barrier_observed,
    )
    return attempt_result, route_result


__all__ = ["run_transition_races", "transition_race_definitions"]
