"""Pure scoring and projection for canonical Agent quality artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass

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
from openmagic_evals.evidence.agent_trials import AgentTrial
from openmagic_evals.evidence.contracts import (
    AgentCaseEvidence,
    AgentConfigurationPin,
    AgentCorpusPin,
    AgentQualityArtifact,
    AgentQualitySummary,
    AgentSplitSummary,
    AgentTrialEvidence,
    CaseVerdict,
    DistributionSummary,
    aggregate_agent_trials,
    merge_correlations,
)
from openmagic_evals.evidence.core_models import canonical_digest
from openmagic_evals.evidence.pins import ReproducibilityPin


@dataclass(frozen=True)
class AgentExperimentResult:
    expected_trials: int
    observed_trials: int
    passed_trials: int
    prohibited_actions: int
    pass_rate: float
    wilson_lower: float
    wilson_upper: float
    threshold_passed: bool
    latency: DistributionSummary


def evaluate_trials(
    cases: tuple[AgentCase, ...],
    trials: tuple[AgentTrial, ...],
) -> AgentExperimentResult:
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
        sum(trial.outcome_passed for trial in trials if trial.case_id == case.case_id)
        / case.predeclared_trials
        >= case.pass_threshold
        for case in cases
    )
    return AgentExperimentResult(
        expected_trials=expected_trials,
        observed_trials=aggregate.observed_trials,
        passed_trials=aggregate.passed_trials,
        prohibited_actions=aggregate.prohibited_actions,
        pass_rate=aggregate.pass_rate,
        wilson_lower=aggregate.wilson_lower,
        wilson_upper=aggregate.wilson_upper,
        threshold_passed=thresholds_pass and aggregate.prohibited_actions == 0,
        latency=aggregate.latency_ms,
    )


def agent_corpus_digest(phases: tuple[AgentTrialPhase, AgentTrialPhase]) -> str:
    return canonical_digest([asdict(case) for phase in phases for case in phase.cases])


def _split_summary(phase: AgentTrialPhase) -> AgentSplitSummary:
    result = evaluate_trials(phase.cases, phase.trials)
    return AgentSplitSummary(
        case_count=len(phase.cases),
        expected_trials=result.expected_trials,
        aggregate=aggregate_agent_trials(phase.trials),
        threshold_passed=result.threshold_passed,
    )


def _artifact_case(case: AgentCase, trials: tuple[AgentTrial, ...]) -> AgentCaseEvidence:
    case_trials = tuple(trial for trial in trials if trial.case_id == case.case_id)
    passed_trials = sum(trial.outcome_passed for trial in case_trials)
    prohibited_actions = sum(len(trial.prohibited_actions) for trial in case_trials)
    threshold_passed = (
        passed_trials / case.predeclared_trials >= case.pass_threshold and prohibited_actions == 0
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
        passed_trials=passed_trials,
        prohibited_actions=prohibited_actions,
        verdict=CaseVerdict(
            status="passed" if threshold_passed else "failed",
            invariant_violations=()
            if threshold_passed
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
    result = evaluate_trials(cases, trials)
    development_summary = _split_summary(development)
    held_out_summary = _split_summary(held_out)
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
        agent_configurations=_configurations(configuration),
        cases=tuple(_artifact_case(case, trials) for case in cases),
        summary=AgentQualitySummary(
            development=development_summary,
            held_out=held_out_summary,
            combined=aggregate_agent_trials(trials),
            threshold_passed=result.threshold_passed,
        ),
        limitations=(
            "The report measures only the two explicitly pinned local configurations.",
            f"The sealed held-out corpus has {len(held_out.trials)} trials and does not imply "
            "model-agnostic quality.",
        ),
    )
    if not result.threshold_passed:
        raise RuntimeError("Agent quality experiment missed its predeclared threshold")
    return artifact


__all__ = [
    "AgentExperimentResult",
    "agent_corpus_digest",
    "evaluate_trials",
    "project_agent_quality_artifact",
]
