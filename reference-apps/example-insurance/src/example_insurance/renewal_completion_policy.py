"""Application Policy for evidence-backed renewal completion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from example_insurance.renewal_effect_policy import EffectCertainty

CompletionStepState = Literal["pending", "succeeded", "failed", "cancelled"]


def completion_step_state(value: object) -> CompletionStepState:
    if value == "pending":
        return "pending"
    if value == "succeeded":
        return "succeeded"
    if value == "failed":
        return "failed"
    if value == "cancelled":
        return "cancelled"
    raise RuntimeError("Workflow Step has an invalid completion state")


@dataclass(frozen=True)
class CompletionStepFact:
    state: CompletionStepState
    has_accepted_output: bool


@dataclass(frozen=True)
class CompletionEffectFact:
    certainty: EffectCertainty
    has_applied_evidence: bool


class RenewalCompletionPolicy:
    @staticmethod
    def is_complete(
        *,
        steps: tuple[CompletionStepFact, ...],
        effects: tuple[CompletionEffectFact, ...],
    ) -> bool:
        return (
            bool(steps)
            and all(step.state == "succeeded" and step.has_accepted_output for step in steps)
            and bool(effects)
            and all(
                effect.certainty == "applied" and effect.has_applied_evidence for effect in effects
            )
        )


__all__ = [
    "CompletionEffectFact",
    "CompletionStepFact",
    "CompletionStepState",
    "RenewalCompletionPolicy",
    "completion_step_state",
]
