"""Recorded real-transaction race corpora for cardinality-one invariants."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

from example_insurance.renewals import (
    CancelRenewalOutreach,
    CancelRenewalOutreachInput,
    ExampleInsurance,
    StartRenewalOutreachResult,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
    SubmitVerificationCodeResult,
)
from openmagic_runtime.commands import Cause, CommandReceipt
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence.contracts import Correlations
from openmagic_evals.evidence.inspection import EvidenceInspection
from openmagic_evals.evidence.race_claims import run_claim_races
from openmagic_evals.evidence.race_models import (
    RaceCorpus,
    RaceSeedResult,
    jitter_pair,
    race_digest,
)
from openmagic_evals.evidence.race_processes import run_process_contenders
from openmagic_evals.evidence.race_transitions import run_transition_races
from openmagic_evals.harness import renewal_context
from openmagic_evals.harness.renewal_scenario import prepare_synthetic_renewal_start
from openmagic_evals.harness.verification_scenario import issue_verification_challenge


def run_command_receipt_races(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> RaceCorpus:
    results: list[RaceSeedResult] = []
    inspection = EvidenceInspection(database_url)
    for seed in seeds:
        command = prepare_synthetic_renewal_start(application, threads, seed)
        jitters = jitter_pair(seed, 0)
        contenders = run_process_contenders(
            database_url,
            case_id="race.command-receipt",
            seed=seed,
            jitter_microseconds=jitters,
            operation="command_receipt",
            payloads=(command, command),
        )
        receipts = cast(
            tuple[
                CommandReceipt[StartRenewalOutreachResult],
                CommandReceipt[StartRenewalOutreachResult],
            ],
            tuple(result.require_value() for result in contenders.results),
        )
        if receipts[0] != receipts[1]:
            raise AssertionError(f"Command replay differed for seed {seed}")
        receipt = receipts[0]
        count = inspection.command_receipts(command.command_id)
        if count != 1:
            raise AssertionError(f"Command receipt constraint disagreed for seed {seed}")
        results.append(
            RaceSeedResult(
                seed=seed,
                jitter_microseconds=jitters,
                public_outcomes=("value_identical_receipt", "value_identical_receipt"),
                constraint_rows=count,
                correlations=Correlations(
                    command_ids=(command.command_id,),
                    workflow_ids=(command.input.workflow_id,),
                    instance_ids=(receipt.result.instance_id,),
                    process_ids=contenders.process_ids,
                ),
                observation_digest=race_digest(
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
        )
    return RaceCorpus(
        case_id="race.command-receipt",
        uses_overlap_barrier=True,
        varied_jitter=True,
        database_constraint="openmagic_runtime.command_receipts(command_id)",
        expected_public_outcomes=("value_identical_receipt", "value_identical_receipt"),
        results=tuple(results),
    )


def run_verification_submission_races(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> RaceCorpus:
    results: list[RaceSeedResult] = []
    inspection = EvidenceInspection(database_url)
    for seed in seeds:
        scenario = issue_verification_challenge(application, threads)
        challenge_id = scenario.challenge_receipt.result.challenge_id
        code = scenario.code
        if challenge_id is None or code is None:
            raise AssertionError("Verification race setup did not produce a Challenge code")
        commands = tuple(
            SubmitVerificationCode(
                command_id=uuid4(),
                actor=scenario.actor,
                cause=Cause("message", str(uuid4())),
                input=SubmitVerificationCodeInput(
                    challenge_id=challenge_id,
                    protected_command_id=scenario.protected_command.command_id,
                    workflow_id=scenario.renewal.input.workflow_id,
                    thread_id=scenario.renewal.input.thread_id,
                    purpose="renewal.read_approved_details",
                    code=code,
                ),
            )
            for _ in range(2)
        )
        jitters = jitter_pair(seed, 100_000)
        contenders = run_process_contenders(
            database_url,
            case_id="race.verification-submission",
            seed=seed,
            jitter_microseconds=jitters,
            operation="verification_submission",
            payloads=(
                (commands[0], b"synthetic-issue71-race-secret"),
                (commands[1], b"synthetic-issue71-race-secret"),
            ),
        )
        receipts = cast(
            tuple[
                CommandReceipt[SubmitVerificationCodeResult],
                CommandReceipt[SubmitVerificationCodeResult],
            ],
            tuple(result.require_value() for result in contenders.results),
        )
        outcomes = tuple(sorted(receipt.result.verification_outcome for receipt in receipts))
        if outcomes != ("already_used", "verified"):
            raise AssertionError(f"Verification race public outcomes disagreed for seed {seed}")
        count = inspection.verification_sessions(challenge_id)
        if count != 1:
            raise AssertionError(f"Verification session constraint disagreed for seed {seed}")
        results.append(
            RaceSeedResult(
                seed=seed,
                jitter_microseconds=jitters,
                public_outcomes=outcomes,
                constraint_rows=count,
                correlations=Correlations(
                    command_ids=tuple(command.command_id for command in commands),
                    workflow_ids=(scenario.renewal.input.workflow_id,),
                    verification_challenge_ids=(challenge_id,),
                    verification_session_ids=tuple(
                        receipt.result.session_id
                        for receipt in receipts
                        if receipt.result.session_id is not None
                    ),
                    process_ids=contenders.process_ids,
                ),
                observation_digest=race_digest(
                    {
                        "seed": seed,
                        "outcomes": outcomes,
                        "constraint_rows": count,
                        "challenge_id": str(challenge_id),
                    }
                ),
                contender_process_ids=contenders.process_ids,
                overlap_barrier_observed=contenders.overlap_barrier_observed,
            )
        )
        protected_delivery = application.run_delivery_worker_once(
            worker_id=f"verification-race-cleanup-{seed}"
        )
        if protected_delivery is None:
            raise AssertionError(f"Verification protected Delivery was missing for seed {seed}")
        cancellation = application.cancel_renewal_outreach(
            CancelRenewalOutreach(
                command_id=uuid4(),
                actor=scenario.actor,
                cause=Cause("command", str(uuid4())),
                input=CancelRenewalOutreachInput(scenario.renewal.input.workflow_id),
            )
        )
        if cancellation.result.outcome != "cancelled":
            raise AssertionError(f"Verification race cleanup failed for seed {seed}")
    return RaceCorpus(
        case_id="race.verification-submission",
        uses_overlap_barrier=True,
        varied_jitter=True,
        database_constraint="example_insurance.verification_sessions(challenge_id)",
        expected_public_outcomes=("already_used", "verified"),
        results=tuple(results),
    )


def run_all_races(*, seeds: tuple[int, ...] = tuple(range(100))) -> tuple[RaceCorpus, ...]:
    with renewal_context(verification_code_secret=b"synthetic-issue71-race-secret") as (
        database_url,
        application,
        threads,
    ):
        command = run_command_receipt_races(
            database_url,
            application,
            threads,
            seeds=seeds,
        )
    with renewal_context(verification_code_secret=b"synthetic-issue71-race-secret") as (
        database_url,
        application,
        threads,
    ):
        step, delivery = run_claim_races(
            database_url,
            application,
            threads,
            seeds=seeds,
        )
    with renewal_context(verification_code_secret=b"synthetic-issue71-race-secret") as (
        database_url,
        _application,
        _threads,
    ):
        signal, attempt, route = run_transition_races(database_url, seeds=seeds)
    with renewal_context(verification_code_secret=b"synthetic-issue71-race-secret") as (
        database_url,
        application,
        threads,
    ):
        verification = run_verification_submission_races(
            database_url,
            application,
            threads,
            seeds=seeds,
        )
    return command, delivery, step, signal, attempt, route, verification


__all__ = [
    "RaceCorpus",
    "RaceSeedResult",
    "run_all_races",
    "run_command_receipt_races",
    "run_verification_submission_races",
]
