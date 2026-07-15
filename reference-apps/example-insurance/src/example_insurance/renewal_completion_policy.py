"""Application Policy for evidence-backed renewal completion."""

from __future__ import annotations

from dataclasses import dataclass

from openmagic_runtime.kernel.records import StepState

from example_insurance.renewal_effect_policy import EffectCertainty


@dataclass(frozen=True)
class CompletionStepFact:
    state: StepState
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
    "RenewalCompletionPolicy",
]
