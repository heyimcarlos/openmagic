from __future__ import annotations

from openmagic_evals.evidence.matrix import (
    DETERMINISTIC_RELEASE_MATRIX,
    REQUIRED_EVIDENCE_FAMILIES,
    cardinality_one_races,
)


def test_release_matrix_covers_every_accepted_deterministic_family() -> None:
    assert {
        "acknowledgement",
        "completion",
        "definition",
        "domain_event",
        "exact_thread_delivery",
        "external_effect",
        "lease",
        "recovery",
        "replay",
        "retry",
        "route",
        "signal",
        "transaction",
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
        "race.step-claim",
        "race.wait-signal",
        "race.verification-submission",
    }
    assert all(race.uses_overlap_barrier for race in races)
    assert all(race.seeds == tuple(range(100)) for race in races)
    assert all(race.varied_jitter for race in races)
    assert all(race.database_constraint for race in races)
