"""Typed per-seed observations for cardinality-one race evidence."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Literal

from pydantic import JsonValue, TypeAdapter

from openmagic_evals.evidence.contracts import Correlations, race_trial_digest

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


__all__ = ["RaceCorpus", "RaceSeedResult", "jitter_pair", "race_observation"]
