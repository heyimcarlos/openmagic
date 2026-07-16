from __future__ import annotations

import pickle

from openmagic_evals.evidence.contracts import has_correlations
from openmagic_evals.evidence.race_processes import ProcessRaceFailure
from openmagic_evals.evidence.races import run_all_races
from openmagic_runtime.kernel.control import SignalConflict, SignalConflictReason


def test_signal_conflict_type_and_reason_survive_process_transport() -> None:
    failure = ProcessRaceFailure.capture(
        SignalConflict(SignalConflictReason.WAIT_ALREADY_SATISFIED)
    )

    transported = pickle.loads(pickle.dumps(failure))

    assert transported.exception_type is SignalConflict
    assert transported.reason is SignalConflictReason.WAIT_ALREADY_SATISFIED


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
        assert all(result.overlap_barrier_observed for result in corpus.results)
        assert all(len(set(result.contender_process_ids)) == 2 for result in corpus.results)
        assert all(has_correlations(result.correlations) for result in corpus.results)
