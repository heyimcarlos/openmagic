from __future__ import annotations

from openmagic_evals.evidence.races import run_all_races


def test_all_cardinality_races_record_actual_trials() -> None:
    corpora = run_all_races(seeds=(0, 1))

    assert {corpus.case_id for corpus in corpora} == {
        "race.attempt-result",
        "race.command-receipt",
        "race.delivery-claim",
        "race.route-activation",
        "race.step-claim",
        "race.verification-submission",
        "race.wait-signal",
    }
    for corpus in corpora:
        assert corpus.uses_overlap_barrier
        assert corpus.varied_jitter
        assert tuple(result.seed for result in corpus.results) == (0, 1)
        assert all(len(result.jitter_microseconds) == 2 for result in corpus.results)
        assert all(len(result.public_outcomes) == 2 for result in corpus.results)
        assert all(result.constraint_rows == 1 for result in corpus.results)
        assert all(result.observation_digest.startswith("sha256:") for result in corpus.results)
        assert all(
            any(result.correlations.model_dump(mode="python").values()) for result in corpus.results
        )
