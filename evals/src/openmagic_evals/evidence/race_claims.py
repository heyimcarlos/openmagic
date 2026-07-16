"""Recorded Step and Delivery claim race corpora."""

from __future__ import annotations

from uuid import uuid4

from example_insurance.renewals import ExampleInsurance
from openmagic_runtime.delivery import ClaimedDelivery
from openmagic_runtime.kernel.work import ClaimedAttempt
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence._definition_correlations import renewal_instance_definition
from openmagic_evals.evidence.contracts import (
    ApplicationCorrelations,
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
from openmagic_evals.evidence.race_processes import (
    DeliveryClaimRace,
    StepClaimRace,
    run_process_contenders,
)
from openmagic_evals.harness import prepare_synthetic_renewal_start


def run_claim_races(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> tuple[RaceCorpus, RaceCorpus]:
    inspection = EvidenceInspection(database_url)
    step_results: list[RaceSeedResult] = []
    delivery_results: list[RaceSeedResult] = []
    for seed in seeds:
        command = prepare_synthetic_renewal_start(application, threads, 20_000 + seed)
        receipt = application.start_renewal_outreach(command)
        step_jitters = jitter_pair(seed, 200_000)
        step_workers = (f"race-step-{seed}-0", f"race-step-{seed}-1")
        step_contenders = run_process_contenders(
            database_url,
            case_id="race.step-claim",
            seed=seed,
            jitter_microseconds=step_jitters,
            requests=(
                StepClaimRace(step_workers[0], uuid4()),
                StepClaimRace(step_workers[1], uuid4()),
            ),
        )
        step_claims: tuple[ClaimedAttempt | None, ClaimedAttempt | None] = (
            step_contenders.results[0].require_value(),
            step_contenders.results[1].require_value(),
        )
        step_winners = tuple(item for item in step_claims if item is not None)
        if len(step_winners) != 1:
            raise AssertionError(f"Step claim cardinality disagreed for seed {seed}")
        step_winner = step_winners[0]
        step_count = inspection.step_running_attempts(step_winner.step_id)
        if step_count != 1:
            raise AssertionError(f"Step constraint disagreed for seed {seed}")
        step_outcomes = tuple(
            "claimed" if item is not None else "not_claimed" for item in step_claims
        )
        step_result = race_observation(
            {
                "seed": seed,
                "jitter_microseconds": step_jitters,
                "public_outcomes": step_outcomes,
                "constraint_rows": step_count,
                "attempt_id": str(step_winner.attempt_id),
            }
        )
        step_results.append(
            RaceSeedResult(
                seed=seed,
                jitter_microseconds=step_jitters,
                public_outcomes=step_outcomes,
                constraint_rows=step_count,
                correlations=Correlations(
                    runtime=RuntimeCorrelations(
                        command_ids=(command.command_id,),
                        workflow_ids=(command.input.workflow_id,),
                        instance_ids=(receipt.result.instance_id,),
                        instance_definitions=(
                            renewal_instance_definition(receipt.result.instance_id),
                        ),
                        step_ids=(step_winner.step_id,),
                        attempt_ids=(step_winner.attempt_id,),
                    ),
                    process=ProcessCorrelations(
                        worker_ids=step_workers, process_ids=step_contenders.process_ids
                    ),
                ),
                observation=step_result,
                contender_process_ids=step_contenders.process_ids,
                overlap_barrier_observed=step_contenders.overlap_barrier_observed,
            )
        )
        step_worker = next(
            f"race-step-{seed}-{index}"
            for index, item in enumerate(step_claims)
            if item is not None
        )
        application.complete_workflow_attempt(attempt=step_winner, worker_id=step_worker)
        application.run_workflow_worker_once(worker_id=f"race-draft-{seed}")

        delivery_jitters = jitter_pair(seed, 300_000)
        delivery_workers = (
            f"race-delivery-{seed}-0",
            f"race-delivery-{seed}-1",
        )
        delivery_contenders = run_process_contenders(
            database_url,
            case_id="race.delivery-claim",
            seed=seed,
            jitter_microseconds=delivery_jitters,
            requests=(
                DeliveryClaimRace(delivery_workers[0], uuid4()),
                DeliveryClaimRace(delivery_workers[1], uuid4()),
            ),
        )
        delivery_claims: tuple[ClaimedDelivery | None, ClaimedDelivery | None] = (
            delivery_contenders.results[0].require_value(),
            delivery_contenders.results[1].require_value(),
        )
        delivery_winners = tuple(item for item in delivery_claims if item is not None)
        if len(delivery_winners) != 1:
            raise AssertionError(f"Delivery claim cardinality disagreed for seed {seed}")
        delivery_winner = delivery_winners[0]
        delivery_count = inspection.delivery_running_attempts(delivery_winner.delivery_id)
        if delivery_count != 1:
            raise AssertionError(f"Delivery constraint disagreed for seed {seed}")
        delivery_outcomes = tuple(
            "claimed" if item is not None else "not_claimed" for item in delivery_claims
        )
        delivery_result = race_observation(
            {
                "seed": seed,
                "jitter_microseconds": delivery_jitters,
                "public_outcomes": delivery_outcomes,
                "constraint_rows": delivery_count,
                "delivery_attempt_id": str(delivery_winner.delivery_attempt_id),
            }
        )
        delivery_results.append(
            RaceSeedResult(
                seed=seed,
                jitter_microseconds=delivery_jitters,
                public_outcomes=delivery_outcomes,
                constraint_rows=delivery_count,
                correlations=Correlations(
                    application=ApplicationCorrelations(
                        thread_ids=(delivery_winner.thread_id,),
                        delivery_ids=(delivery_winner.delivery_id,),
                        delivery_attempt_ids=(delivery_winner.delivery_attempt_id,),
                    ),
                    process=ProcessCorrelations(
                        worker_ids=delivery_workers,
                        process_ids=delivery_contenders.process_ids,
                    ),
                ),
                observation=delivery_result,
                contender_process_ids=delivery_contenders.process_ids,
                overlap_barrier_observed=delivery_contenders.overlap_barrier_observed,
            )
        )
        delivery_worker = next(
            f"race-delivery-{seed}-{index}"
            for index, item in enumerate(delivery_claims)
            if item is not None
        )
        application.complete_delivery_attempt(
            claim=delivery_winner,
            worker_id=delivery_worker,
        )

    return (
        RaceCorpus(
            case_id="race.step-claim",
            uses_overlap_barrier=True,
            varied_jitter=True,
            database_constraint="one_leased_attempt_per_step",
            expected_public_outcomes=("claimed", "not_claimed"),
            results=tuple(step_results),
        ),
        RaceCorpus(
            case_id="race.delivery-claim",
            uses_overlap_barrier=True,
            varied_jitter=True,
            database_constraint="one_running_delivery_attempt",
            expected_public_outcomes=("claimed", "not_claimed"),
            results=tuple(delivery_results),
        ),
    )


__all__ = ["run_claim_races"]
