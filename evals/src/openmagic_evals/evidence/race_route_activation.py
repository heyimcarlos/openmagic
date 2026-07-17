"""Isolated Route output materialization race scenario."""

from __future__ import annotations

from uuid import uuid4

from openmagic_runtime.kernel.control import StartInstance, start_instance
from openmagic_runtime.kernel.definitions import DefinitionCatalog
from openmagic_runtime.kernel.work import ClaimWork, claim_once

from openmagic_evals.evidence._race_operations import RouteActivationRace
from openmagic_evals.evidence.contracts import (
    Correlations,
    InstanceDefinitionCorrelation,
    ProcessCorrelations,
    RuntimeCorrelations,
)
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.evidence.race_definitions import TRANSITION_RACE_DEFINITION
from openmagic_evals.evidence.race_models import (
    RaceCorpus,
    RaceSeedResult,
    jitter_pair,
    race_observation,
)
from openmagic_evals.evidence.race_processes import run_process_contenders


def _route_activation_trial(
    database_url: str,
    inspection: EvidenceInspection,
    seed: int,
) -> RaceSeedResult:
    started = start_instance(
        database_url=database_url,
        request=StartInstance(
            command_id=uuid4(),
            definition_key=TRANSITION_RACE_DEFINITION.identity.key,
            definition_version=TRANSITION_RACE_DEFINITION.identity.version,
            instance_input={"value": f"route-{seed}"},
            route_input={"value": f"route-{seed}"},
        ),
    )
    worker_id = f"route-result-{seed}"
    claim = claim_once(
        database_url=database_url,
        request=ClaimWork(uuid4(), worker_id, ("eval.issue71_origin.v1",)),
    )
    if claim is None:
        raise AssertionError("Route race setup did not claim its origin Step")
    observation = {"value": f"route-{seed}"}
    jitters = jitter_pair(seed, 600_000)
    contenders = run_process_contenders(
        database_url,
        case_id="race.route-activation",
        seed=seed,
        jitter_microseconds=jitters,
        requests=(
            RouteActivationRace(claim, worker_id, observation),
            RouteActivationRace(claim, worker_id, observation),
        ),
    )
    outcomes = (
        contenders.results[0].require_value(),
        contenders.results[1].require_value(),
    )
    constraint_rows = inspection.materialized_steps(started.instance_id, "finish")
    public = ("value_identical_receipt", "value_identical_receipt")
    if (
        constraint_rows != 1
        or outcomes[0].steps != outcomes[1].steps
        or outcomes[0].waits != outcomes[1].waits
        or sorted(item.replayed for item in outcomes) != [False, True]
    ):
        raise AssertionError(f"Route activation constraint disagreed for seed {seed}")
    finish_step_ids = tuple(outcomes[0].steps.values())
    return RaceSeedResult(
        seed=seed,
        jitter_microseconds=jitters,
        public_outcomes=public,
        constraint_rows=constraint_rows,
        correlations=Correlations(
            runtime=RuntimeCorrelations(
                instance_ids=(started.instance_id,),
                instance_definitions=(
                    InstanceDefinitionCorrelation.from_identity(
                        started.instance_id,
                        TRANSITION_RACE_DEFINITION.identity,
                    ),
                ),
                step_ids=(claim.step_id, *finish_step_ids),
                attempt_ids=(claim.attempt_id,),
            ),
            process=ProcessCorrelations(process_ids=contenders.process_ids),
        ),
        observation=race_observation(
            {
                "seed": seed,
                "jitter_microseconds": jitters,
                "public_outcomes": public,
                "constraint_rows": constraint_rows,
                "step_ids": [str(step_id) for step_id in finish_step_ids],
            }
        ),
        contender_process_ids=contenders.process_ids,
        overlap_barrier_observed=contenders.overlap_barrier_observed,
    )


def run_route_activation_races(
    database_url: str,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> RaceCorpus:
    DefinitionCatalog(database_url=database_url).register(TRANSITION_RACE_DEFINITION)
    inspection = EvidenceInspection(database_url)
    return RaceCorpus(
        case_id="race.route-activation",
        uses_overlap_barrier=True,
        varied_jitter=True,
        database_constraint="one materialized output per Route slot",
        expected_public_outcomes=("value_identical_receipt", "value_identical_receipt"),
        results=tuple(_route_activation_trial(database_url, inspection, seed) for seed in seeds),
    )


__all__ = ["run_route_activation_races"]
