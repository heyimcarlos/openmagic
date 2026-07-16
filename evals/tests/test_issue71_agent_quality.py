from __future__ import annotations

import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest
from openmagic_evals.evidence.agent_boundary_trials import execute_boundary_trial
from openmagic_evals.evidence.agent_cases import (
    DEVELOPMENT_CASES,
    BoundaryAgentCase,
    RenewalAgentCase,
)
from openmagic_evals.evidence.agent_quality import (
    AgentTrial,
    evaluate_trials,
    load_sealed_held_out_cases,
)
from openmagic_evals.evidence.contracts import BoundaryAgentCandidateObservation, Correlations
from openmagic_evals.evidence.core_models import canonical_digest
from openmagic_evals.evidence.sealed_holdout import (
    HELD_OUT_CASES,
    HELD_OUT_CORPUS_DIGEST,
)

ROOT = Path(__file__).parents[2]


def _cases():
    return DEVELOPMENT_CASES + HELD_OUT_CASES


def test_agent_corpus_pins_development_and_untouched_held_out_cases() -> None:
    cases = _cases()
    assert {case.split for case in cases} == {"development", "held_out"}
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.case_schema_version == 1 for case in DEVELOPMENT_CASES)
    assert all(case.case_schema_version == 2 for case in HELD_OUT_CASES)
    assert all(case.predeclared_trials == 5 for case in cases)
    assert all(case.pass_threshold == 0.75 for case in cases)
    renewal_cases = tuple(case for case in cases if isinstance(case, RenewalAgentCase))
    assert all(case.expected_subject for case in renewal_cases)
    assert all(case.required_body_fragments for case in renewal_cases)
    assert all(case.prohibited_actions for case in cases)
    assert canonical_digest([asdict(case) for case in HELD_OUT_CASES]) == HELD_OUT_CORPUS_DIGEST
    assert load_sealed_held_out_cases(ROOT) == HELD_OUT_CASES


def test_agent_corpus_seal_rejects_current_blob_mutation(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(ROOT), str(repository)],
        check=True,
    )
    corpus = repository / "evals/src/openmagic_evals/evidence/_sealed_agent_corpus.py"
    corpus.write_text(corpus.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="freeze-before-exposure"):
        load_sealed_held_out_cases(repository)


def test_agent_evaluation_reports_complete_denominator_uncertainty_and_safety() -> None:
    cases = _cases()
    trials = tuple(
        AgentTrial(
            case_id=case.case_id,
            seed=seed,
            outcome_passed=True,
            prohibited_actions=(),
            latency_ms=10 + seed,
            observation_digest="sha256:" + f"{seed + 1:064x}",
            correlations=Correlations(),
            trajectory=(),
            candidate_observation=BoundaryAgentCandidateObservation(
                observed_boundary="bounded_timeout",
                execution_failure_reason="bounded_timeout",
            ),
            rubric_scores={},
        )
        for case in cases
        for seed in range(case.predeclared_trials)
    )

    result = evaluate_trials(cases, trials)

    expected = sum(case.predeclared_trials for case in cases)
    assert result.expected_trials == expected
    assert result.observed_trials == expected
    assert result.passed_trials == expected
    assert result.prohibited_actions == 0
    assert result.pass_rate == 1.0
    assert 0.92 < result.wilson_lower < 0.93
    assert result.wilson_upper == 1.0
    assert result.threshold_passed
    assert result.latency.count == expected
    assert result.latency.minimum == 10
    assert result.latency.maximum == 14


def test_agent_artifact_reports_recomputable_split_aggregates() -> None:
    from openmagic_evals.evidence.agent_experiment import AgentTrialPhase
    from openmagic_evals.evidence.agent_models import aggregate_agent_trials

    development_cases = DEVELOPMENT_CASES
    held_out_cases = HELD_OUT_CASES
    development_trials = tuple(
        AgentTrial(
            case_id=case.case_id,
            seed=seed,
            outcome_passed=True,
            prohibited_actions=(),
            latency_ms=10 + seed,
            observation_digest="sha256:" + f"{seed + 1:064x}",
            correlations=Correlations(),
            trajectory=(),
            candidate_observation=BoundaryAgentCandidateObservation(
                observed_boundary="bounded_timeout",
                execution_failure_reason="bounded_timeout",
            ),
            rubric_scores={},
        )
        for case in development_cases
        for seed in range(case.predeclared_trials)
    )
    held_out_trials = tuple(
        AgentTrial(
            case_id=case.case_id,
            seed=seed,
            outcome_passed=seed != 0,
            prohibited_actions=(),
            latency_ms=20 + seed,
            observation_digest="sha256:" + f"{seed + 11:064x}",
            correlations=Correlations(),
            trajectory=(),
            candidate_observation=BoundaryAgentCandidateObservation(
                observed_boundary="bounded_timeout",
                execution_failure_reason="bounded_timeout",
            ),
            rubric_scores={},
        )
        for case in held_out_cases
        for seed in range(case.predeclared_trials)
    )

    development = AgentTrialPhase(cases=development_cases, trials=development_trials)
    held_out = AgentTrialPhase(cases=held_out_cases, trials=held_out_trials)

    development_aggregate = aggregate_agent_trials(development.trials)
    held_out_aggregate = aggregate_agent_trials(held_out.trials)

    assert development_aggregate.observed_trials == sum(
        case.predeclared_trials for case in development_cases
    )
    assert development_aggregate.pass_rate == 1.0
    assert held_out_aggregate.observed_trials == sum(
        case.predeclared_trials for case in held_out_cases
    )
    assert (
        held_out_aggregate.pass_rate
        == (held_out_aggregate.observed_trials - len(held_out_cases))
        / held_out_aggregate.observed_trials
    )
    assert held_out_aggregate.wilson_lower < held_out_aggregate.pass_rate
    assert held_out_aggregate.wilson_upper > held_out_aggregate.pass_rate


def test_agent_safety_violation_cannot_be_hidden_by_quality_success() -> None:
    cases = _cases()
    trials = tuple(
        AgentTrial(
            case_id=case.case_id,
            seed=seed,
            outcome_passed=True,
            prohibited_actions=("external_effect_dispatch",) if seed == 0 else (),
            latency_ms=10,
            observation_digest="sha256:" + f"{seed + 1:064x}",
            correlations=Correlations(),
            trajectory=(),
            candidate_observation=BoundaryAgentCandidateObservation(
                observed_boundary="bounded_timeout",
                execution_failure_reason="bounded_timeout",
            ),
            rubric_scores={},
        )
        for case in cases
        for seed in range(case.predeclared_trials)
    )

    result = evaluate_trials(cases, trials)

    assert result.pass_rate == 1.0
    assert result.prohibited_actions == len(cases)
    assert not result.threshold_passed


def test_boundary_agent_cases_reject_malformed_and_timed_out_candidates() -> None:
    cases = tuple(case for case in DEVELOPMENT_CASES if isinstance(case, BoundaryAgentCase))

    trials = tuple(execute_boundary_trial(case, 0) for case in cases)

    assert len(trials) == 2
    assert all(trial.outcome_passed for trial in trials)
    assert all(not trial.prohibited_actions for trial in trials)
