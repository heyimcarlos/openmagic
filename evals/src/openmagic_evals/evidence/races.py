"""Composition of isolated cardinality-one race scenarios."""

from openmagic_evals.evidence.race_attempt_result import run_attempt_result_races
from openmagic_evals.evidence.race_command_receipt import run_command_receipt_races
from openmagic_evals.evidence.race_delivery_claim import run_delivery_claim_races
from openmagic_evals.evidence.race_models import RaceCorpus
from openmagic_evals.evidence.race_route_activation import run_route_activation_races
from openmagic_evals.evidence.race_signal import run_signal_races
from openmagic_evals.evidence.race_step_claim import run_step_claim_races
from openmagic_evals.evidence.race_verification_submission import (
    run_verification_submission_races,
)
from openmagic_evals.harness import renewal_context

_RACE_SECRET = b"synthetic-issue71-race-secret"


def run_all_races(*, seeds: tuple[int, ...] = tuple(range(100))) -> tuple[RaceCorpus, ...]:
    with renewal_context(verification_code_secret=_RACE_SECRET) as (
        database_url,
        application,
        threads,
    ):
        command = run_command_receipt_races(database_url, application, threads, seeds=seeds)
    with renewal_context(verification_code_secret=_RACE_SECRET) as (
        database_url,
        application,
        threads,
    ):
        delivery = run_delivery_claim_races(database_url, application, threads, seeds=seeds)
    with renewal_context(verification_code_secret=_RACE_SECRET) as (
        database_url,
        application,
        threads,
    ):
        step = run_step_claim_races(database_url, application, threads, seeds=seeds)
    with renewal_context(verification_code_secret=_RACE_SECRET) as (
        database_url,
        _application,
        _threads,
    ):
        signal = run_signal_races(database_url, seeds=seeds)
    with renewal_context(verification_code_secret=_RACE_SECRET) as (
        database_url,
        _application,
        _threads,
    ):
        attempt = run_attempt_result_races(database_url, seeds=seeds)
    with renewal_context(verification_code_secret=_RACE_SECRET) as (
        database_url,
        _application,
        _threads,
    ):
        route = run_route_activation_races(database_url, seeds=seeds)
    with renewal_context(verification_code_secret=_RACE_SECRET) as (
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


__all__ = ["run_all_races"]
