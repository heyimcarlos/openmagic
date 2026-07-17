"""Command receipt cardinality-one race scenario."""

from __future__ import annotations

from example_insurance.renewals import ExampleInsurance
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence._definition_correlations import renewal_instance_definition
from openmagic_evals.evidence._race_operations import StartRenewalOutreachRace
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
from openmagic_evals.harness.renewal_scenario import prepare_synthetic_renewal_start


def _command_receipt_trial(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    inspection: EvidenceInspection,
    seed: int,
) -> RaceSeedResult:
    command = prepare_synthetic_renewal_start(application, threads, seed)
    jitters = jitter_pair(seed, 0)
    contenders = run_process_contenders(
        database_url,
        case_id="race.command-receipt",
        seed=seed,
        jitter_microseconds=jitters,
        requests=(StartRenewalOutreachRace(command), StartRenewalOutreachRace(command)),
    )
    receipts = tuple(item.require_value() for item in contenders.results)
    if receipts[0] != receipts[1]:
        raise AssertionError(f"Command replay differed for seed {seed}")
    count = inspection.command_receipts(command.command_id)
    if count != 1:
        raise AssertionError(f"Command receipt constraint disagreed for seed {seed}")
    receipt = receipts[0]
    return RaceSeedResult(
        seed=seed,
        jitter_microseconds=jitters,
        public_outcomes=("value_identical_receipt", "value_identical_receipt"),
        constraint_rows=count,
        correlations=Correlations(
            runtime=RuntimeCorrelations(
                command_ids=(command.command_id,),
                workflow_ids=(command.input.workflow_id,),
                instance_ids=(receipt.result.instance_id,),
                instance_definitions=(renewal_instance_definition(receipt.result.instance_id),),
            ),
            process=ProcessCorrelations(process_ids=contenders.process_ids),
        ),
        observation=race_observation(
            {
                "seed": seed,
                "same_receipt": True,
                "constraint_rows": count,
                "command_id": str(command.command_id),
            }
        ),
        contender_process_ids=contenders.process_ids,
        overlap_barrier_observed=contenders.overlap_barrier_observed,
    )


def run_command_receipt_races(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> RaceCorpus:
    inspection = EvidenceInspection(database_url)
    return RaceCorpus(
        case_id="race.command-receipt",
        uses_overlap_barrier=True,
        varied_jitter=True,
        database_constraint="openmagic_runtime.command_receipts(command_id)",
        expected_public_outcomes=("value_identical_receipt", "value_identical_receipt"),
        results=tuple(
            _command_receipt_trial(database_url, application, threads, inspection, seed)
            for seed in seeds
        ),
    )


__all__ = ["run_command_receipt_races"]
