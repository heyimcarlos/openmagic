"""Verification Session cardinality-one race scenario."""

from __future__ import annotations

from uuid import uuid4

from example_insurance.renewals import (
    CancelRenewalOutreach,
    CancelRenewalOutreachInput,
    ExampleInsurance,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
)
from openmagic_runtime.commands import Cause
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.evidence._race_operations import VerificationSubmissionRace
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
from openmagic_evals.evidence.race_processes import run_process_contenders
from openmagic_evals.harness.verification_scenario import (
    VerificationScenario,
    issue_verification_challenge,
)

_RACE_SECRET = b"synthetic-issue71-race-secret"


def _verification_submission_trial(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    inspection: EvidenceInspection,
    seed: int,
) -> RaceSeedResult:
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
        requests=(
            VerificationSubmissionRace(commands[0], _RACE_SECRET),
            VerificationSubmissionRace(commands[1], _RACE_SECRET),
        ),
    )
    receipts = tuple(item.require_value() for item in contenders.results)
    outcomes = tuple(sorted(receipt.result.verification_outcome for receipt in receipts))
    if outcomes != ("already_used", "verified"):
        raise AssertionError(f"Verification race public outcomes disagreed for seed {seed}")
    count = inspection.verification_sessions(challenge_id)
    if count != 1:
        raise AssertionError(f"Verification session constraint disagreed for seed {seed}")
    result = RaceSeedResult(
        seed=seed,
        jitter_microseconds=jitters,
        public_outcomes=outcomes,
        constraint_rows=count,
        correlations=Correlations(
            runtime=RuntimeCorrelations(
                command_ids=tuple(command.command_id for command in commands),
                workflow_ids=(scenario.renewal.input.workflow_id,),
            ),
            application=ApplicationCorrelations(
                verification_challenge_ids=(challenge_id,),
                verification_session_ids=tuple(
                    receipt.result.session_id
                    for receipt in receipts
                    if receipt.result.session_id is not None
                ),
            ),
            process=ProcessCorrelations(process_ids=contenders.process_ids),
        ),
        observation=race_observation(
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
    _complete_scenario(application, scenario, seed)
    return result


def _complete_scenario(
    application: ExampleInsurance,
    scenario: VerificationScenario,
    seed: int,
) -> None:
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


def run_verification_submission_races(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> RaceCorpus:
    inspection = EvidenceInspection(database_url)
    return RaceCorpus(
        case_id="race.verification-submission",
        uses_overlap_barrier=True,
        varied_jitter=True,
        database_constraint="example_insurance.verification_sessions(challenge_id)",
        expected_public_outcomes=("already_used", "verified"),
        results=tuple(
            _verification_submission_trial(database_url, application, threads, inspection, seed)
            for seed in seeds
        ),
    )


__all__ = ["run_verification_submission_races"]
