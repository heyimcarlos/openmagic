"""Sanitized Agent trial and case evidence contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from openmagic_evals.evidence.agent_scoring import (
    AgentCandidateObservation,
    AgentScorerContract,
    agent_rubric_scores,
)
from openmagic_evals.evidence.core_models import (
    ArtifactCaseBase,
    Correlations,
    EvidenceModel,
    canonical_digest,
    has_correlations,
    require_digest,
)


class SanitizedAgentEvent(EvidenceModel):
    sequence: int = Field(gt=0)
    event_type: Literal["context_projection", "candidate", "outcome_verification"]
    durable_identity: str
    input_digest: str
    output_digest: str

    @model_validator(mode="after")
    def validate_event(self) -> SanitizedAgentEvent:
        if not self.durable_identity:
            raise ValueError("Agent trajectory event requires one durable identity")
        require_digest(self.input_digest, "Agent trajectory input digest")
        require_digest(self.output_digest, "Agent trajectory output digest")
        return self


class AgentTrialEvidence(EvidenceModel):
    seed: int = Field(ge=0)
    outcome_passed: bool
    prohibited_actions: tuple[str, ...]
    latency_ms: int = Field(ge=0)
    trajectory_digest: str
    correlations: Correlations
    trajectory: tuple[SanitizedAgentEvent, ...] = Field(min_length=3)
    candidate_observation: AgentCandidateObservation
    rubric_scores: dict[str, bool]

    @model_validator(mode="after")
    def validate_trial(self) -> AgentTrialEvidence:
        require_digest(self.trajectory_digest, "Agent trajectory digest")
        if not has_correlations(self.correlations):
            raise ValueError("Agent trial must retain durable correlations")
        if tuple(event.sequence for event in self.trajectory) != tuple(
            range(1, len(self.trajectory) + 1)
        ) or tuple(event.event_type for event in self.trajectory) != (
            "context_projection",
            "candidate",
            "outcome_verification",
        ):
            raise ValueError("Agent trajectory must retain its complete ordered lifecycle")
        if not self.rubric_scores or self.outcome_passed != all(self.rubric_scores.values()):
            raise ValueError("Agent outcome must derive from every recorded rubric score")
        document = {
            "candidate_observation": self.candidate_observation.model_dump(mode="json"),
            "rubric_scores": dict(sorted(self.rubric_scores.items())),
            "trajectory": [event.model_dump(mode="json") for event in self.trajectory],
        }
        if self.trajectory_digest != canonical_digest(document):
            raise ValueError("Agent trajectory digest does not match its sanitized events")
        return self


class AgentCaseEvidence(ArtifactCaseBase):
    case_kind: Literal["agent"] = "agent"
    configuration_key: str
    split: Literal["development", "held_out"]
    prohibited_action_contract: tuple[str, ...] = Field(min_length=1)
    scorer_contract: AgentScorerContract
    agent_trials: tuple[AgentTrialEvidence, ...] = Field(min_length=1)
    pass_threshold: float = Field(ge=0.0, le=1.0)
    passed_trials: int = Field(ge=0)
    prohibited_actions: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_trials(self) -> AgentCaseEvidence:
        if tuple(trial.seed for trial in self.agent_trials) != self.seeds:
            raise ValueError("Agent trials must follow the predeclared seed corpus")
        if tuple(trial.trajectory_digest for trial in self.agent_trials) != (
            self.observation_digests
        ):
            raise ValueError("Agent trials must own every recorded trajectory digest")
        if self.passed_trials > self.observed_trials:
            raise ValueError("Agent case pass count exceeds its denominator")
        if any(
            set(trial.prohibited_actions).difference(self.prohibited_action_contract)
            for trial in self.agent_trials
        ):
            raise ValueError("Agent trial contains an action outside its predeclared contract")
        if any(
            trial.rubric_scores
            != agent_rubric_scores(
                self.scorer_contract,
                trial.candidate_observation,
                trial.prohibited_actions,
            )
            for trial in self.agent_trials
        ):
            raise ValueError("Agent trial scores must be recomputable from sanitized evidence")
        return self


__all__ = [
    "AgentCaseEvidence",
    "AgentTrialEvidence",
    "SanitizedAgentEvent",
]
