"""Canonical contracts for deterministic cardinality-one race evidence."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from openmagic_evals.evidence.core_models import (
    ArtifactCaseBase,
    Correlations,
    EvidenceModel,
    canonical_digest,
    merge_correlations,
    validate_correlated_definitions,
)
from openmagic_evals.evidence.pins import ReproducibilityPin
from openmagic_evals.evidence.release_models import (
    SCHEMA_VERSION,
    DeterministicSummary,
    validate_deterministic_summary,
)

_OBSERVATION_ADAPTER = TypeAdapter(dict[str, JsonValue])


def race_observation(value: object) -> dict[str, JsonValue]:
    return _OBSERVATION_ADAPTER.validate_json(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    )


def jitter_pair(seed: int, offset: int) -> tuple[int, int]:
    return (
        round(random.Random(seed * 2 + offset).random() * 1000),
        round(random.Random(seed * 2 + offset + 1).random() * 1000),
    )


def race_trial_digest(
    *,
    seed: int,
    jitter_microseconds: tuple[int, int],
    public_outcomes: tuple[str, ...],
    constraint_rows: int,
    correlations: Correlations,
    observation: dict[str, JsonValue],
    contender_process_ids: tuple[int, int],
    overlap_barrier_observed: bool,
) -> str:
    return canonical_digest(
        {
            "seed": seed,
            "jitter_microseconds": jitter_microseconds,
            "public_outcomes": public_outcomes,
            "constraint_rows": constraint_rows,
            "correlations": correlations.model_dump(mode="json"),
            "observation": observation,
            "contender_process_ids": contender_process_ids,
            "overlap_barrier_observed": overlap_barrier_observed,
        }
    )


class RaceTrialEvidence(EvidenceModel):
    seed: int = Field(ge=0)
    jitter_microseconds: tuple[int, int]
    public_outcomes: tuple[str, ...] = Field(min_length=2)
    constraint_rows: int = Field(ge=0)
    correlations: Correlations
    observation_digest: str
    observation: dict[str, JsonValue]
    contender_process_ids: tuple[int, int]
    overlap_barrier_observed: Literal[True]

    @model_validator(mode="after")
    def validate_race_trial(self) -> RaceTrialEvidence:
        if any(value < 0 for value in self.jitter_microseconds):
            raise ValueError("race trial must record two non-negative jitter values")
        if self.constraint_rows != 1:
            raise ValueError("race trial must record exactly one PostgreSQL constraint row")
        if len(set(self.contender_process_ids)) != 2 or any(
            process_id <= 0 for process_id in self.contender_process_ids
        ):
            raise ValueError("race trial must record two fresh contender interpreters")
        durable_ids = (
            self.correlations.runtime.command_ids,
            self.correlations.runtime.workflow_ids,
            self.correlations.runtime.instance_ids,
            self.correlations.runtime.step_ids,
            self.correlations.runtime.attempt_ids,
            self.correlations.runtime.wait_ids,
            self.correlations.runtime.signal_ids,
            self.correlations.application.delivery_ids,
            self.correlations.application.verification_challenge_ids,
        )
        if not any(durable_ids):
            raise ValueError("race trial must correlate its public and PostgreSQL outcomes")
        if self.observation_digest != race_trial_digest(
            seed=self.seed,
            jitter_microseconds=self.jitter_microseconds,
            public_outcomes=self.public_outcomes,
            constraint_rows=self.constraint_rows,
            correlations=self.correlations,
            observation=self.observation,
            contender_process_ids=self.contender_process_ids,
            overlap_barrier_observed=self.overlap_barrier_observed,
        ):
            raise ValueError("race trial digest does not match its canonical observation")
        return self


class RaceCase(ArtifactCaseBase):
    case_kind: Literal["race"] = "race"
    race_trials: tuple[RaceTrialEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_trials(self) -> RaceCase:
        if tuple(trial.seed for trial in self.race_trials) != self.seeds:
            raise ValueError("race trials must follow the predeclared seed corpus")
        if tuple(trial.observation_digest for trial in self.race_trials) != (
            self.observation_digests
        ):
            raise ValueError("race trials must own every recorded observation digest")
        if self.correlations != merge_correlations(
            trial.correlations for trial in self.race_trials
        ):
            raise ValueError("race case correlations must derive from every trial")
        return self


class RaceArtifact(EvidenceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["race_corpus"] = "race_corpus"
    lane: Literal["deterministic_correctness"] = "deterministic_correctness"
    reproducibility: ReproducibilityPin
    cases: tuple[RaceCase, ...] = Field(min_length=1)
    summary: DeterministicSummary
    limitations: tuple[str, ...]
    negative_claims: tuple[str, ...]

    @model_validator(mode="after")
    def validate_release(self) -> RaceArtifact:
        validate_deterministic_summary(self.cases, self.summary, self.negative_claims)
        validate_correlated_definitions(
            (case.correlations for case in self.cases),
            self.reproducibility.definition_digests,
        )
        return self


@dataclass(frozen=True)
class RaceSeedResult:
    seed: int
    jitter_microseconds: tuple[int, int]
    public_outcomes: tuple[str, ...]
    constraint_rows: int
    correlations: Correlations
    observation: dict[str, JsonValue]
    contender_process_ids: tuple[int, int]
    overlap_barrier_observed: Literal[True]

    @property
    def observation_digest(self) -> str:
        return race_trial_digest(
            seed=self.seed,
            jitter_microseconds=self.jitter_microseconds,
            public_outcomes=self.public_outcomes,
            constraint_rows=self.constraint_rows,
            correlations=self.correlations,
            observation=self.observation,
            contender_process_ids=self.contender_process_ids,
            overlap_barrier_observed=self.overlap_barrier_observed,
        )


@dataclass(frozen=True)
class RaceCorpus:
    case_id: str
    uses_overlap_barrier: bool
    varied_jitter: bool
    database_constraint: str
    expected_public_outcomes: tuple[str, str]
    results: tuple[RaceSeedResult, ...]


__all__ = [
    "RaceArtifact",
    "RaceCase",
    "RaceCorpus",
    "RaceSeedResult",
    "RaceTrialEvidence",
    "jitter_pair",
    "race_observation",
    "race_trial_digest",
]
