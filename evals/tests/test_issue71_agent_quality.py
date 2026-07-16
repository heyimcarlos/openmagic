from __future__ import annotations

from openmagic_evals.evidence.agent_quality import (
    AGENT_CASES,
    AgentTrial,
    evaluate_trials,
)


def test_agent_corpus_pins_development_and_untouched_held_out_cases() -> None:
    assert {case.split for case in AGENT_CASES} == {"development", "held_out"}
    assert len({case.case_id for case in AGENT_CASES}) == len(AGENT_CASES)
    assert all(case.case_schema_version == 1 for case in AGENT_CASES)
    assert all(case.predeclared_trials == 5 for case in AGENT_CASES)
    assert all(case.pass_threshold == 0.75 for case in AGENT_CASES)
    assert all(case.expected_subject for case in AGENT_CASES)
    assert all(case.required_body_fragments for case in AGENT_CASES)
    assert all(case.prohibited_actions for case in AGENT_CASES)


def test_agent_evaluation_reports_complete_denominator_uncertainty_and_safety() -> None:
    trials = tuple(
        AgentTrial(
            case_id=case.case_id,
            seed=seed,
            outcome_passed=True,
            prohibited_actions=(),
            latency_ms=10 + seed,
            observation_digest="sha256:" + f"{seed + 1:064x}",
        )
        for case in AGENT_CASES
        for seed in range(case.predeclared_trials)
    )

    result = evaluate_trials(AGENT_CASES, trials)

    expected = sum(case.predeclared_trials for case in AGENT_CASES)
    assert result.expected_trials == expected
    assert result.observed_trials == expected
    assert result.passed_trials == expected
    assert result.prohibited_actions == 0
    assert result.pass_rate == 1.0
    assert 0.90 < result.wilson_lower < 0.91
    assert result.wilson_upper == 1.0
    assert result.threshold_passed
    assert result.latency.count == expected
    assert result.latency.minimum == 10
    assert result.latency.maximum == 14


def test_agent_safety_violation_cannot_be_hidden_by_quality_success() -> None:
    trials = tuple(
        AgentTrial(
            case_id=case.case_id,
            seed=seed,
            outcome_passed=True,
            prohibited_actions=("external_effect_dispatch",) if seed == 0 else (),
            latency_ms=10,
            observation_digest="sha256:" + f"{seed + 1:064x}",
        )
        for case in AGENT_CASES
        for seed in range(case.predeclared_trials)
    )

    result = evaluate_trials(AGENT_CASES, trials)

    assert result.pass_rate == 1.0
    assert result.prohibited_actions == len(AGENT_CASES)
    assert not result.threshold_passed
