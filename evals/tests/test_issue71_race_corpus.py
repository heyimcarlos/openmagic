from __future__ import annotations

import pickle
from uuid import uuid4

import pytest
from openmagic_evals.evidence.contracts import has_correlations
from openmagic_evals.evidence.race_processes import (
    AcceptSignalRace,
    ProcessRaceCompleted,
    ProcessRaceFailure,
    ProcessRaceSucceeded,
    RaceFailureKind,
    RaceFailureReason,
    RaceProtocolError,
    StepClaimRace,
    decode_process_result,
    validate_race_pair,
    validate_race_request,
)
from openmagic_evals.evidence.races import run_all_races
from openmagic_runtime.kernel.control import (
    AcceptSignal,
    SignalConflict,
    SignalConflictReason,
)


def test_signal_conflict_type_and_reason_survive_process_transport() -> None:
    failure = ProcessRaceFailure.capture(
        SignalConflict(SignalConflictReason.WAIT_ALREADY_SATISFIED)
    )

    transported = pickle.loads(pickle.dumps(failure))

    assert transported.kind is RaceFailureKind.SIGNAL_CONFLICT
    assert transported.reason is RaceFailureReason.WAIT_ALREADY_SATISFIED


def test_process_result_decoder_rejects_legacy_untyped_tuple() -> None:
    request = AcceptSignalRace(
        AcceptSignal(
            uuid4(),
            uuid4(),
            uuid4(),
            "eval.issue71.decision",
            1,
            {"value": "approve"},
            "approve",
        )
    )

    with pytest.raises(RaceProtocolError, match="completion envelope"):
        decode_process_result(request, ("result", 123, None, None))


def test_process_failure_rejects_invalid_kind_reason_combination() -> None:
    with pytest.raises(RaceProtocolError, match="invalid failure reason"):
        ProcessRaceFailure(
            kind=RaceFailureKind.SIGNAL_CONFLICT,
            reason=RaceFailureReason.UNCLASSIFIED,
            message="invalid wire failure",
        )


def test_process_result_decoder_rejects_request_result_mismatch() -> None:
    request = AcceptSignalRace(
        AcceptSignal(
            uuid4(),
            uuid4(),
            uuid4(),
            "eval.issue71.decision",
            1,
            {"value": "approve"},
            "approve",
        )
    )
    message = ProcessRaceCompleted(
        process_id=123,
        outcome=ProcessRaceSucceeded(value=None),
    )

    with pytest.raises(RaceProtocolError, match="Signal receipt"):
        decode_process_result(request, message)


def test_process_pair_rejects_mixed_request_kinds() -> None:
    signal = AcceptSignalRace(
        AcceptSignal(
            uuid4(),
            uuid4(),
            uuid4(),
            "eval.issue71.decision",
            1,
            {"value": "approve"},
            "approve",
        )
    )

    with pytest.raises(RaceProtocolError, match="same typed operation"):
        validate_race_pair((signal, StepClaimRace("worker-1", uuid4())))


def test_request_decoder_rejects_unknown_operation_and_invalid_fields() -> None:
    with pytest.raises(RaceProtocolError, match="closed protocol"):
        validate_race_request(object())

    with pytest.raises(RaceProtocolError, match="worker ID"):
        validate_race_pair(
            (
                StepClaimRace("", uuid4()),
                StepClaimRace("worker-2", uuid4()),
            )
        )


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
