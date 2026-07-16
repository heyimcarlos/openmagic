"""Shared in-memory result from one predeclared Agent trial."""

from __future__ import annotations

from dataclasses import dataclass

from openmagic_evals.evidence.contracts import Correlations, SanitizedAgentEvent


@dataclass(frozen=True)
class AgentTrial:
    case_id: str
    seed: int
    outcome_passed: bool
    prohibited_actions: tuple[str, ...]
    latency_ms: int
    observation_digest: str
    correlations: Correlations
    trajectory: tuple[SanitizedAgentEvent, ...]
    rubric_scores: dict[str, bool]


__all__ = ["AgentTrial"]
