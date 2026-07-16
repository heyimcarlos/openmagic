from __future__ import annotations

from pathlib import Path

from openmagic_evals.evidence.deterministic_observations import release_observations
from openmagic_evals.evidence.matrix import (
    DETERMINISTIC_RELEASE_MATRIX,
    REQUIRED_EVIDENCE_FAMILIES,
    cardinality_one_races,
)
from openmagic_evals.evidence.release import _release_case


def test_release_matrix_covers_every_accepted_deterministic_family() -> None:
    assert {
        "acknowledgement",
        "completion",
        "definition",
        "domain_event",
        "exact_thread_delivery",
        "external_effect",
        "executor",
        "lease",
        "recovery",
        "replay",
        "retry",
        "route",
        "signal",
        "transaction",
        "trace_completeness",
        "wait",
    } == REQUIRED_EVIDENCE_FAMILIES
    assert {case.family for case in DETERMINISTIC_RELEASE_MATRIX} == REQUIRED_EVIDENCE_FAMILIES
    assert len({case.case_id for case in DETERMINISTIC_RELEASE_MATRIX}) == len(
        DETERMINISTIC_RELEASE_MATRIX
    )
    assert all(case.pytest_nodes for case in DETERMINISTIC_RELEASE_MATRIX)
    assert all(case.pass_condition for case in DETERMINISTIC_RELEASE_MATRIX)


def test_every_cardinality_one_race_declares_barrier_and_100_varied_jitter_seeds() -> None:
    races = cardinality_one_races()

    assert {race.case_id for race in races} == {
        "race.command-receipt",
        "race.delivery-claim",
        "race.attempt-result",
        "race.route-activation",
        "race.step-claim",
        "race.wait-signal",
        "race.verification-submission",
    }
    assert all(race.uses_overlap_barrier for race in races)
    assert all(race.seeds == tuple(range(100)) for race in races)
    assert all(race.varied_jitter for race in races)
    assert all(race.database_constraint for race in races)


def test_release_observations_are_durable_and_do_not_consume_demo_artifacts(
    tmp_path: Path,
) -> None:
    observations = release_observations(tmp_path)

    assert set(observations) == REQUIRED_EVIDENCE_FAMILIES
    assert all(
        any(observation.correlations.model_dump(mode="python").values())
        for observation in observations.values()
    )
    trace = observations["trace_completeness"].correlations
    assert all(
        (
            trace.command_ids,
            trace.workflow_ids,
            trace.instance_ids,
            trace.step_ids,
            trace.attempt_ids,
            trace.wait_ids,
            trace.signal_ids,
            trace.trace_event_ids,
            trace.thread_ids,
            trace.message_ids,
            trace.agent_run_ids,
            trace.domain_event_ids,
            trace.delivery_ids,
            trace.delivery_attempt_ids,
            trace.external_effect_ids,
            trace.approval_grant_ids,
            trace.verification_challenge_ids,
            trace.verification_session_ids,
            trace.worker_ids,
            trace.process_ids,
            trace.provider_request_ids,
        )
    )


def test_release_case_fails_closed_when_one_exact_node_is_missing(tmp_path: Path) -> None:
    case = DETERMINISTIC_RELEASE_MATRIX[1]
    observation = release_observations(tmp_path)[case.family]
    tests = {case.pytest_nodes[0]: {"status": "passed"}}

    artifact_case = _release_case(case, tests, observation)

    assert artifact_case.verdict.status == "infrastructure_error"
