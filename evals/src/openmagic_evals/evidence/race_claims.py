"""Recorded Step and Delivery claim race corpora."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from example_insurance.renewals import ExampleInsurance
from openmagic_runtime.delivery import ClaimedDelivery
from openmagic_runtime.kernel.work import ClaimedAttempt
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence.contracts import Correlations
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.evidence.race_models import (
    RaceCorpus,
    RaceSeedResult,
    jitter_pair,
    race_digest,
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
    with ThreadPoolExecutor(max_workers=2) as executor:
        for seed in seeds:
            command = prepare_synthetic_renewal_start(application, threads, 20_000 + seed)
            receipt = application.start_renewal_outreach(command)
            step_barrier = Barrier(2)
            step_jitters = jitter_pair(seed, 200_000)

            def claim_step(
                index: int,
                barrier: Barrier = step_barrier,
                jitters: tuple[int, int] = step_jitters,
                race_seed: int = seed,
            ) -> ClaimedAttempt | None:
                barrier.wait()
                time.sleep(jitters[index] / 1_000_000)
                return application.claim_workflow_attempt(
                    worker_id=f"race-step-{race_seed}-{index}",
                    claim_request_id=uuid4(),
                )

            step_claims = tuple(executor.map(claim_step, range(2)))
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
            step_result = {
                "seed": seed,
                "jitter_microseconds": step_jitters,
                "public_outcomes": step_outcomes,
                "constraint_rows": step_count,
                "attempt_id": step_winner.attempt_id,
            }
            step_results.append(
                RaceSeedResult(
                    seed=seed,
                    jitter_microseconds=step_jitters,
                    public_outcomes=step_outcomes,
                    constraint_rows=step_count,
                    correlations=Correlations(
                        command_ids=(command.command_id,),
                        workflow_ids=(command.input.workflow_id,),
                        instance_ids=(receipt.result.instance_id,),
                        step_ids=(step_winner.step_id,),
                        attempt_ids=(step_winner.attempt_id,),
                        worker_ids=tuple(f"race-step-{seed}-{index}" for index in range(2)),
                    ),
                    observation_digest=race_digest(step_result),
                )
            )
            step_worker = next(
                f"race-step-{seed}-{index}"
                for index, item in enumerate(step_claims)
                if item is not None
            )
            application.complete_workflow_attempt(attempt=step_winner, worker_id=step_worker)
            application.run_workflow_worker_once(worker_id=f"race-draft-{seed}")

            delivery_barrier = Barrier(2)
            delivery_jitters = jitter_pair(seed, 300_000)

            def claim_delivery(
                index: int,
                barrier: Barrier = delivery_barrier,
                jitters: tuple[int, int] = delivery_jitters,
                race_seed: int = seed,
            ) -> ClaimedDelivery | None:
                barrier.wait()
                time.sleep(jitters[index] / 1_000_000)
                return application.claim_delivery_attempt(
                    worker_id=f"race-delivery-{race_seed}-{index}",
                    claim_request_id=uuid4(),
                )

            delivery_claims = tuple(executor.map(claim_delivery, range(2)))
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
            delivery_result = {
                "seed": seed,
                "jitter_microseconds": delivery_jitters,
                "public_outcomes": delivery_outcomes,
                "constraint_rows": delivery_count,
                "delivery_attempt_id": delivery_winner.delivery_attempt_id,
            }
            delivery_results.append(
                RaceSeedResult(
                    seed=seed,
                    jitter_microseconds=delivery_jitters,
                    public_outcomes=delivery_outcomes,
                    constraint_rows=delivery_count,
                    correlations=Correlations(
                        thread_ids=(delivery_winner.thread_id,),
                        delivery_ids=(delivery_winner.delivery_id,),
                        delivery_attempt_ids=(delivery_winner.delivery_attempt_id,),
                        worker_ids=tuple(f"race-delivery-{seed}-{index}" for index in range(2)),
                    ),
                    observation_digest=race_digest(delivery_result),
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
