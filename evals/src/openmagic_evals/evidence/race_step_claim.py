"""Step Attempt claim cardinality-one race scenario."""

from __future__ import annotations

from uuid import uuid4

from example_insurance.renewals import ExampleInsurance
from openmagic_runtime.kernel.work import ClaimedAttempt
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence._definition_correlations import renewal_instance_definition
from openmagic_evals.evidence._race_operations import StepClaimRace
from openmagic_evals.evidence.contracts import (
    Correlations,
    ProcessCorrelations,
    RuntimeCorrelations,
)
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.evidence.race_models import (
    RaceCorpus,
    RaceSeedResult,
    jitter_pair,
    race_observation,
)
from openmagic_evals.evidence.race_processes import run_process_contenders
from openmagic_evals.harness import prepare_synthetic_renewal_start


def _step_claim_trial(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    inspection: EvidenceInspection,
    seed: int,
) -> RaceSeedResult:
    command = prepare_synthetic_renewal_start(application, threads, 20_000 + seed)
    receipt = application.start_renewal_outreach(command)
    jitters = jitter_pair(seed, 200_000)
    workers = (f"race-step-{seed}-0", f"race-step-{seed}-1")
    contenders = run_process_contenders(
        database_url,
        case_id="race.step-claim",
        seed=seed,
        jitter_microseconds=jitters,
        requests=(
            StepClaimRace(workers[0], uuid4()),
            StepClaimRace(workers[1], uuid4()),
        ),
    )
    claims: tuple[ClaimedAttempt | None, ClaimedAttempt | None] = (
        contenders.results[0].require_value(),
        contenders.results[1].require_value(),
    )
    winners = tuple(item for item in claims if item is not None)
    if len(winners) != 1:
        raise AssertionError(f"Step claim cardinality disagreed for seed {seed}")
    winner = winners[0]
    count = inspection.step_running_attempts(winner.step_id)
    if count != 1:
        raise AssertionError(f"Step constraint disagreed for seed {seed}")
    outcomes = tuple("claimed" if item is not None else "not_claimed" for item in claims)
    return RaceSeedResult(
        seed=seed,
        jitter_microseconds=jitters,
        public_outcomes=outcomes,
        constraint_rows=count,
        correlations=Correlations(
            runtime=RuntimeCorrelations(
                command_ids=(command.command_id,),
                workflow_ids=(command.input.workflow_id,),
                instance_ids=(receipt.result.instance_id,),
                instance_definitions=(renewal_instance_definition(receipt.result.instance_id),),
                step_ids=(winner.step_id,),
                attempt_ids=(winner.attempt_id,),
            ),
            process=ProcessCorrelations(worker_ids=workers, process_ids=contenders.process_ids),
        ),
        observation=race_observation(
            {
                "seed": seed,
                "jitter_microseconds": jitters,
                "public_outcomes": outcomes,
                "constraint_rows": count,
                "attempt_id": str(winner.attempt_id),
            }
        ),
        contender_process_ids=contenders.process_ids,
        overlap_barrier_observed=contenders.overlap_barrier_observed,
    )


def run_step_claim_races(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> RaceCorpus:
    inspection = EvidenceInspection(database_url)
    return RaceCorpus(
        case_id="race.step-claim",
        uses_overlap_barrier=True,
        varied_jitter=True,
        database_constraint="one_leased_attempt_per_step",
        expected_public_outcomes=("claimed", "not_claimed"),
        results=tuple(
            _step_claim_trial(database_url, application, threads, inspection, seed)
            for seed in seeds
        ),
    )


__all__ = ["run_step_claim_races"]
