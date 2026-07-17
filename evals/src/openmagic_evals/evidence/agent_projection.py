"""Pure scoring and projection for canonical Agent quality artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from openmagic_evals.evidence.agent_aggregation import (
    AgentAggregate,
    aggregate_agent_trials,
    assess_agent_case,
    summarize_agent_quality,
)
from openmagic_evals.evidence.agent_artifact import (
    AgentConfigurationPin,
    AgentCorpusPin,
    AgentQualityArtifact,
)
from openmagic_evals.evidence.agent_cases import (
    BOUNDARY_AGENT_KEY,
    DEVELOPMENT_CASES,
    RENEWAL_AGENT_KEY,
    AgentCase,
)
from openmagic_evals.evidence.agent_corpus_phase import HeldOutCorpusPhase
from openmagic_evals.evidence.agent_experiment import (
    AgentConfigurationPhase,
    AgentTrialPhase,
    agent_scorer_contract,
)
from openmagic_evals.evidence.agent_trial_models import AgentCaseEvidence, AgentTrialEvidence
from openmagic_evals.evidence.agent_trials import AgentTrial
from openmagic_evals.evidence.core_models import CaseVerdict, canonical_digest, merge_correlations
from openmagic_evals.evidence.pins import ReproducibilityPin


@dataclass(frozen=True)
class AgentExperimentAssessment:
    expected_trials: int
    aggregate: AgentAggregate
    threshold_passed: bool


def evaluate_trials(
    cases: tuple[AgentCase, ...],
    trials: tuple[AgentTrial, ...],
) -> AgentExperimentAssessment:
    expected_trials = sum(case.predeclared_trials for case in cases)
    if len(trials) != expected_trials:
        raise ValueError("Agent experiment is missing trials from its denominator")
    case_by_id = {case.case_id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("Agent case identities must be unique")
    seen: set[tuple[str, int]] = set()
    for trial in trials:
        case = case_by_id.get(trial.case_id)
        if case is None:
            raise ValueError("Agent trial references an unknown case")
        identity = (trial.case_id, trial.seed)
        if identity in seen or trial.seed not in range(case.predeclared_trials):
            raise ValueError("Agent trial seed is duplicated or outside the predeclared corpus")
        seen.add(identity)
    for case in cases:
        case_trials = tuple(trial for trial in trials if trial.case_id == case.case_id)
        if len(case_trials) != case.predeclared_trials:
            raise ValueError("Agent case does not have its complete trial denominator")

    aggregate = aggregate_agent_trials(trials)
    thresholds_pass = all(
        assess_agent_case(
            tuple(trial for trial in trials if trial.case_id == case.case_id),
            expected_trials=case.predeclared_trials,
            pass_threshold=case.pass_threshold,
        ).threshold_passed
        for case in cases
    )
    return AgentExperimentAssessment(
        expected_trials=expected_trials,
        aggregate=aggregate,
        threshold_passed=thresholds_pass and aggregate.prohibited_actions == 0,
    )


def agent_corpus_digest(phases: tuple[AgentTrialPhase, AgentTrialPhase]) -> str:
    return canonical_digest([asdict(case) for phase in phases for case in phase.cases])


def _artifact_case(case: AgentCase, trials: tuple[AgentTrial, ...]) -> AgentCaseEvidence:
    case_trials = tuple(trial for trial in trials if trial.case_id == case.case_id)
    assessment = assess_agent_case(
        case_trials,
        expected_trials=case.predeclared_trials,
        pass_threshold=case.pass_threshold,
    )
    return AgentCaseEvidence(
        case_id=case.case_id,
        case_schema_version=case.case_schema_version,
        configuration_key=case.configuration_key,
        split=case.split,
        prohibited_action_contract=case.prohibited_actions,
        scorer_contract=agent_scorer_contract(case),
        expected_trials=case.predeclared_trials,
        observed_trials=case.predeclared_trials,
        seeds=tuple(range(case.predeclared_trials)),
        correlations=merge_correlations(trial.correlations for trial in case_trials),
        observation_digests=tuple(trial.observation_digest for trial in case_trials),
        agent_trials=tuple(
            AgentTrialEvidence(
                seed=trial.seed,
                outcome_passed=trial.outcome_passed,
                prohibited_actions=trial.prohibited_actions,
                latency_ms=trial.latency_ms,
                trajectory_digest=trial.observation_digest,
                correlations=trial.correlations,
                trajectory=trial.trajectory,
                candidate_observation=trial.candidate_observation,
                rubric_scores=trial.rubric_scores,
            )
            for trial in case_trials
        ),
        pass_threshold=case.pass_threshold,
        passed_trials=assessment.aggregate.passed_trials,
        prohibited_actions=assessment.aggregate.prohibited_actions,
        verdict=CaseVerdict(
            status="passed" if assessment.threshold_passed else "failed",
            invariant_violations=()
            if assessment.threshold_passed
            else ("Agent case missed its predeclared quality or safety threshold",),
        ),
    )


def _configurations(configuration: AgentConfigurationPhase) -> tuple[AgentConfigurationPin, ...]:
    return (
        AgentConfigurationPin(
            agent_key=RENEWAL_AGENT_KEY,
            agent_version=1,
            instruction_digest=configuration.renewal_instruction_digest,
            tool_schema_digest=configuration.renewal_tool_schema_digest,
            provider="openmagic-fresh-interpreter",
            model="deterministic-reference-agent-v1",
            reasoning="deterministic",
            temperature=0.0,
        ),
        AgentConfigurationPin(
            agent_key=BOUNDARY_AGENT_KEY,
            agent_version=1,
            instruction_digest=configuration.boundary_instruction_digest,
            tool_schema_digest=configuration.boundary_tool_schema_digest,
            provider="openmagic-fresh-interpreter",
            model="deterministic-boundary-harness-v1",
            reasoning="none",
            temperature=0.0,
        ),
    )


def project_agent_quality_artifact(
    *,
    development: AgentTrialPhase,
    held_out: AgentTrialPhase,
    configuration: AgentConfigurationPhase,
    seal: HeldOutCorpusPhase,
    reproducibility: ReproducibilityPin,
) -> AgentQualityArtifact:
    """Project completed typed phases without executing trials or reading the seal."""

    if development.cases != DEVELOPMENT_CASES or held_out.cases != seal.cases:
        raise ValueError("Agent projection phases do not match their declared corpora")
    cases = development.cases + held_out.cases
    trials = development.trials + held_out.trials
    projected_cases = tuple(_artifact_case(case, trials) for case in cases)
    configurations = _configurations(configuration)
    summary = summarize_agent_quality(
        projected_cases,
        tuple(item.agent_key for item in configurations),
    )
    artifact = AgentQualityArtifact(
        reproducibility=reproducibility,
        corpus=AgentCorpusPin(
            development_cases_digest=canonical_digest([asdict(case) for case in development.cases]),
            held_out_corpus_version=seal.corpus_version,
            held_out_cases_digest=seal.corpus_digest,
            held_out_sealed_at_commit=seal.sealed_at_commit,
            runner_frozen_at_commit=seal.runner_frozen_at_commit,
            tuning_locked_roots=seal.tuning_locked_roots,
            tuning_locked_source_digest=seal.tuning_locked_source_digest,
            execution_phases=("development", "held_out"),
            tuning_unchanged_after_seal=True,
        ),
        agent_configurations=configurations,
        cases=projected_cases,
        summary=summary,
        limitations=(
            "The report measures only the two explicitly pinned local configurations.",
            f"The sealed held-out corpus has {len(held_out.trials)} trials and does not imply "
            "model-agnostic quality.",
        ),
    )
    if not summary.threshold_passed:
        raise RuntimeError("Agent quality experiment missed its predeclared threshold")
    return artifact


__all__ = [
    "AgentExperimentAssessment",
    "agent_corpus_digest",
    "evaluate_trials",
    "project_agent_quality_artifact",
]
