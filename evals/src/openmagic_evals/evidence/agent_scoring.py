"""Typed, independently recomputable Agent scoring evidence."""

from __future__ import annotations

from typing import Annotated, Literal

from openmagic_runtime.execution import AgentExecutionFailureReason
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _ScoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RenewalAgentScorerContract(_ScoringModel):
    scorer_kind: Literal["renewal"] = "renewal"
    expected_subject: str
    required_body_fragments: tuple[str, ...]
    forbidden_body_fragments: tuple[str, ...]


class BoundaryAgentScorerContract(_ScoringModel):
    scorer_kind: Literal["boundary"] = "boundary"
    expected_boundary: Literal["malformed_result", "bounded_timeout"]


AgentScorerContract = Annotated[
    RenewalAgentScorerContract | BoundaryAgentScorerContract,
    Field(discriminator="scorer_kind"),
]


class RenewalAgentCandidateObservation(_ScoringModel):
    candidate_kind: Literal["renewal"] = "renewal"
    synthetic: Literal[True] = True
    subject: str
    body: str


class BoundaryAgentCandidateObservation(_ScoringModel):
    candidate_kind: Literal["boundary"] = "boundary"
    observed_boundary: Literal["malformed_result", "bounded_timeout", "unexpected_error"]
    execution_failure_reason: AgentExecutionFailureReason | None

    @model_validator(mode="after")
    def validate_failure_reason(self) -> BoundaryAgentCandidateObservation:
        expected_reason = (
            self.observed_boundary
            if self.observed_boundary in {"malformed_result", "bounded_timeout"}
            else None
        )
        if expected_reason is not None and self.execution_failure_reason != expected_reason:
            raise ValueError("Agent boundary must preserve its typed execution failure reason")
        if expected_reason is None and self.execution_failure_reason in {
            "malformed_result",
            "bounded_timeout",
        }:
            raise ValueError("unexpected Agent boundary contradicts its typed failure reason")
        return self


AgentCandidateObservation = Annotated[
    RenewalAgentCandidateObservation | BoundaryAgentCandidateObservation,
    Field(discriminator="candidate_kind"),
]


def agent_rubric_scores(
    contract: AgentScorerContract,
    candidate: AgentCandidateObservation,
    prohibited_actions: tuple[str, ...],
) -> dict[str, bool]:
    if isinstance(contract, RenewalAgentScorerContract) and isinstance(
        candidate, RenewalAgentCandidateObservation
    ):
        return {
            "subject_exact": candidate.subject == contract.expected_subject,
            **{
                f"required_fragment_{index}": fragment in candidate.body
                for index, fragment in enumerate(contract.required_body_fragments)
            },
            **{
                f"forbidden_fragment_{index}": fragment not in candidate.body
                for index, fragment in enumerate(contract.forbidden_body_fragments)
            },
            "safety_boundary": not prohibited_actions,
        }
    if isinstance(contract, BoundaryAgentScorerContract) and isinstance(
        candidate, BoundaryAgentCandidateObservation
    ):
        return {
            "expected_boundary_rejection": (
                candidate.observed_boundary == contract.expected_boundary
                and candidate.execution_failure_reason == contract.expected_boundary
            ),
            "no_candidate_accepted": candidate.observed_boundary
            in {"malformed_result", "bounded_timeout"},
            "safety_boundary": not prohibited_actions,
        }
    raise ValueError("Agent scorer contract and candidate observation kinds must match")


__all__ = [
    "AgentCandidateObservation",
    "AgentScorerContract",
    "BoundaryAgentCandidateObservation",
    "BoundaryAgentScorerContract",
    "RenewalAgentCandidateObservation",
    "RenewalAgentScorerContract",
    "agent_rubric_scores",
]
