"""Isolated Attempt result acceptance and replay race scenario."""

from __future__ import annotations

from uuid import uuid4

import psycopg
from openmagic_runtime.kernel.control import KernelControl, StartInstance, start_instance
from openmagic_runtime.kernel.definitions import DefinitionCatalog
from openmagic_runtime.kernel.work import ClaimWork, DispositionRequired, claim_once

from openmagic_evals.evidence._race_operations import AttemptResultRace
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


def _attempt_result_trial(
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
            instance_input={"value": f"transition-{seed}"},
            route_input={"value": f"transition-{seed}"},
        ),
    )
    worker_id = f"attempt-result-{seed}"
    claim = claim_once(
        database_url=database_url,
        request=ClaimWork(uuid4(), worker_id, ("eval.issue71_origin.v1",)),
    )
    if claim is None:
        raise AssertionError("Attempt-result race setup did not claim its origin Step")
    observation = {"value": f"transition-{seed}"}
    jitters = jitter_pair(seed, 500_000)
    contenders = run_process_contenders(
        database_url,
        case_id="race.attempt-result",
        seed=seed,
        jitter_microseconds=jitters,
        requests=(
            AttemptResultRace(claim, worker_id, observation),
            AttemptResultRace(claim, worker_id, observation),
        ),
    )
    dispositions: tuple[DispositionRequired, DispositionRequired] = (
        contenders.results[0].require_value(),
        contenders.results[1].require_value(),
    )
    public = tuple("replayed" if item.replayed else "accepted" for item in dispositions)
    constraint_rows = inspection.completed_attempts(claim.attempt_id)
    if sorted(public) != ["accepted", "replayed"] or constraint_rows != 1:
        raise AssertionError(f"Attempt-result constraint disagreed for seed {seed}")
    with psycopg.connect(database_url) as connection, connection.transaction():
        KernelControl(connection).succeed(
            next(item for item in dispositions if not item.replayed),
            output=observation,
            outcome_route="finish_after_origin",
            route_input=observation,
        )
    return RaceSeedResult(
        seed=seed,
        jitter_microseconds=jitters,
        public_outcomes=public,
        constraint_rows=constraint_rows,
        correlations=Correlations(
            runtime=RuntimeCorrelations(
                instance_ids=(claim.instance_id,),
                instance_definitions=(
                    InstanceDefinitionCorrelation.from_identity(
                        claim.instance_id,
                        TRANSITION_RACE_DEFINITION.identity,
                    ),
                ),
                step_ids=(claim.step_id,),
                attempt_ids=(claim.attempt_id,),
                trace_event_ids=(started.trace_event_id,),
            ),
            process=ProcessCorrelations(
                worker_ids=(worker_id,),
                process_ids=contenders.process_ids,
            ),
        ),
        observation=race_observation(
            {
                "seed": seed,
                "jitter_microseconds": jitters,
                "public_outcomes": public,
                "constraint_rows": constraint_rows,
                "attempt_id": str(claim.attempt_id),
            }
        ),
        contender_process_ids=contenders.process_ids,
        overlap_barrier_observed=contenders.overlap_barrier_observed,
    )


def run_attempt_result_races(
    database_url: str,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> RaceCorpus:
    DefinitionCatalog(database_url=database_url).register(TRANSITION_RACE_DEFINITION)
    inspection = EvidenceInspection(database_url)
    return RaceCorpus(
        case_id="race.attempt-result",
        uses_overlap_barrier=True,
        varied_jitter=True,
        database_constraint="one accepted result per Attempt",
        expected_public_outcomes=("accepted", "replayed"),
        results=tuple(_attempt_result_trial(database_url, inspection, seed) for seed in seeds),
    )


__all__ = ["run_attempt_result_races"]
