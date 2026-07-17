"""Delivery Attempt claim cardinality-one race scenario."""

from __future__ import annotations

from uuid import uuid4

from example_insurance.renewals import ExampleInsurance
from openmagic_runtime.delivery import ClaimedDelivery
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence._race_operations import DeliveryClaimRace
from openmagic_evals.evidence.contracts import (
    ApplicationCorrelations,
    Correlations,
    ProcessCorrelations,
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


def _prepare_delivery(
    application: ExampleInsurance,
    threads: ThreadStore,
    seed: int,
) -> None:
    command = prepare_synthetic_renewal_start(application, threads, 30_000 + seed)
    application.start_renewal_outreach(command)
    worker_id = f"delivery-setup-{seed}"
    attempt = application.claim_workflow_attempt(
        worker_id=worker_id,
        claim_request_id=uuid4(),
    )
    if attempt is None:
        raise AssertionError("Delivery claim race setup did not claim its Step")
    application.complete_workflow_attempt(attempt=attempt, worker_id=worker_id)
    application.run_workflow_worker_once(worker_id=f"delivery-draft-{seed}")


def _delivery_claim_trial(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    inspection: EvidenceInspection,
    seed: int,
) -> RaceSeedResult:
    _prepare_delivery(application, threads, seed)
    jitters = jitter_pair(seed, 300_000)
    workers = (f"race-delivery-{seed}-0", f"race-delivery-{seed}-1")
    contenders = run_process_contenders(
        database_url,
        case_id="race.delivery-claim",
        seed=seed,
        jitter_microseconds=jitters,
        requests=(
            DeliveryClaimRace(workers[0], uuid4()),
            DeliveryClaimRace(workers[1], uuid4()),
        ),
    )
    claims: tuple[ClaimedDelivery | None, ClaimedDelivery | None] = (
        contenders.results[0].require_value(),
        contenders.results[1].require_value(),
    )
    winners = tuple(item for item in claims if item is not None)
    if len(winners) != 1:
        raise AssertionError(f"Delivery claim cardinality disagreed for seed {seed}")
    winner = winners[0]
    count = inspection.delivery_running_attempts(winner.delivery_id)
    if count != 1:
        raise AssertionError(f"Delivery constraint disagreed for seed {seed}")
    outcomes = tuple("claimed" if item is not None else "not_claimed" for item in claims)
    result = RaceSeedResult(
        seed=seed,
        jitter_microseconds=jitters,
        public_outcomes=outcomes,
        constraint_rows=count,
        correlations=Correlations(
            application=ApplicationCorrelations(
                thread_ids=(winner.thread_id,),
                delivery_ids=(winner.delivery_id,),
                delivery_attempt_ids=(winner.delivery_attempt_id,),
            ),
            process=ProcessCorrelations(worker_ids=workers, process_ids=contenders.process_ids),
        ),
        observation=race_observation(
            {
                "seed": seed,
                "jitter_microseconds": jitters,
                "public_outcomes": outcomes,
                "constraint_rows": count,
                "delivery_attempt_id": str(winner.delivery_attempt_id),
            }
        ),
        contender_process_ids=contenders.process_ids,
        overlap_barrier_observed=contenders.overlap_barrier_observed,
    )
    worker_id = next(workers[index] for index, claim in enumerate(claims) if claim is not None)
    application.complete_delivery_attempt(claim=winner, worker_id=worker_id)
    return result


def run_delivery_claim_races(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> RaceCorpus:
    inspection = EvidenceInspection(database_url)
    return RaceCorpus(
        case_id="race.delivery-claim",
        uses_overlap_barrier=True,
        varied_jitter=True,
        database_constraint="one_running_delivery_attempt",
        expected_public_outcomes=("claimed", "not_claimed"),
        results=tuple(
            _delivery_claim_trial(database_url, application, threads, inspection, seed)
            for seed in seeds
        ),
    )


__all__ = ["run_delivery_claim_races"]
