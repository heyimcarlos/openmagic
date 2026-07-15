from __future__ import annotations

import pytest
from openmagic_evals.evidence.races import (
    run_command_receipt_races,
    run_verification_submission_races,
)
from openmagic_evals.harness import renewal_context


def test_command_receipt_race_corpus() -> None:
    with renewal_context(verification_code_secret=b"synthetic-command-race") as context:
        database_url, application, threads = context

        corpus = run_command_receipt_races(database_url, application, threads)

    assert tuple(result.seed for result in corpus.results) == tuple(range(100))
    assert all(result.constraint_rows == 1 for result in corpus.results)
    assert len({result.observation_digest for result in corpus.results}) == 100


@pytest.mark.timeout(300)
def test_verification_submission_race_corpus() -> None:
    with renewal_context(verification_code_secret=b"synthetic-verification-race") as context:
        database_url, application, threads = context

        corpus = run_verification_submission_races(database_url, application, threads)

    assert tuple(result.seed for result in corpus.results) == tuple(range(100))
    assert all(result.constraint_rows == 1 for result in corpus.results)
    assert len({result.observation_digest for result in corpus.results}) == 100
