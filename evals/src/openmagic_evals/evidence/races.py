"""Recorded real-transaction race corpora for cardinality-one invariants."""

from __future__ import annotations

import hashlib
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
from example_insurance.renewals import (
    CancelRenewalOutreach,
    CancelRenewalOutreachInput,
    ExampleInsurance,
    StartRenewalOutreach,
    StartRenewalOutreachResult,
    SubmitVerificationCode,
    SubmitVerificationCodeInput,
    SubmitVerificationCodeResult,
)
from openmagic_runtime.commands import Cause, CommandReceipt
from openmagic_runtime.threads import ThreadStore

from openmagic_evals.harness.renewal_scenario import prepare_synthetic_renewal_start
from openmagic_evals.harness.verification_scenario import issue_verification_challenge


@dataclass(frozen=True)
class RaceSeedResult:
    seed: int
    public_outcomes: tuple[str, ...]
    constraint_rows: int
    correlation_ids: tuple[UUID, ...]
    observation_digest: str


@dataclass(frozen=True)
class RaceCorpus:
    case_id: str
    uses_overlap_barrier: bool
    varied_jitter: bool
    database_constraint: str
    results: tuple[RaceSeedResult, ...]


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def run_command_receipt_races(
    database_url: str,
    application: ExampleInsurance,
    threads: ThreadStore,
    *,
    seeds: tuple[int, ...] = tuple(range(100)),
) -> RaceCorpus:
    results: list[RaceSeedResult] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        for seed in seeds:
            command = prepare_synthetic_renewal_start(application, threads, seed)
            barrier = Barrier(2)

            def submit(
                index: int,
                race_barrier: Barrier = barrier,
                race_seed: int = seed,
                race_command: StartRenewalOutreach = command,
            ) -> CommandReceipt[StartRenewalOutreachResult]:
                race_barrier.wait()
                time.sleep(random.Random(race_seed * 2 + index).random() / 1000)
                return application.start_renewal_outreach(race_command)

            receipts = tuple(executor.map(submit, range(2)))
            if receipts[0] != receipts[1]:
                raise AssertionError(f"Command replay differed for seed {seed}")
            receipt = receipts[0]
            with psycopg.connect(database_url) as connection:
                row = connection.execute(
                    "SELECT count(*) FROM openmagic_runtime.command_receipts WHERE command_id = %s",
                    (command.command_id,),
                ).fetchone()
            count = int(row[0]) if row is not None else 0
            if count != 1:
                raise AssertionError(f"Command receipt constraint disagreed for seed {seed}")
            results.append(
                RaceSeedResult(
                    seed=seed,
                    public_outcomes=("replayed", "replayed"),
                    constraint_rows=count,
                    correlation_ids=(
                        command.command_id,
                        command.input.workflow_id,
                        receipt.result.instance_id,
                    ),
                    observation_digest=_digest(
                        {
                            "seed": seed,
                            "same_receipt": True,
                            "constraint_rows": count,
                            "command_id": str(command.command_id),
                        }
                    ),
                )
            )
    return RaceCorpus(
        case_id="race.command-receipt",
        uses_overlap_barrier=True,
        varied_jitter=True,
        database_constraint="openmagic_runtime.command_receipts(command_id)",
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
    with ThreadPoolExecutor(max_workers=2) as executor:
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
            barrier = Barrier(2)

            def submit(
                index: int,
                race_barrier: Barrier = barrier,
                race_seed: int = seed,
                race_commands: tuple[SubmitVerificationCode, ...] = commands,
            ) -> CommandReceipt[SubmitVerificationCodeResult]:
                race_barrier.wait()
                time.sleep(random.Random(race_seed * 2 + index + 100_000).random() / 1000)
                return application.submit_verification_code(race_commands[index])

            receipts = tuple(executor.map(submit, range(2)))
            outcomes = tuple(sorted(receipt.result.verification_outcome for receipt in receipts))
            if outcomes != ("already_used", "verified"):
                raise AssertionError(f"Verification race public outcomes disagreed for seed {seed}")
            with psycopg.connect(database_url) as connection:
                row = connection.execute(
                    "SELECT count(*) FROM example_insurance.verification_sessions "
                    "WHERE challenge_id = %s",
                    (challenge_id,),
                ).fetchone()
            count = int(row[0]) if row is not None else 0
            if count != 1:
                raise AssertionError(f"Verification session constraint disagreed for seed {seed}")
            results.append(
                RaceSeedResult(
                    seed=seed,
                    public_outcomes=outcomes,
                    constraint_rows=count,
                    correlation_ids=(
                        challenge_id,
                        scenario.protected_command.command_id,
                        scenario.renewal.input.workflow_id,
                    ),
                    observation_digest=_digest(
                        {
                            "seed": seed,
                            "outcomes": outcomes,
                            "constraint_rows": count,
                            "challenge_id": str(challenge_id),
                        }
                    ),
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
        results=tuple(results),
    )


__all__ = [
    "RaceCorpus",
    "RaceSeedResult",
    "run_command_receipt_races",
    "run_verification_submission_races",
]
