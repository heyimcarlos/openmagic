"""Typed per-seed observations for cardinality-one race evidence."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Literal

from openmagic_evals.evidence.contracts import Correlations


def race_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


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
    observation_digest: str
    contender_process_ids: tuple[int, int]
    overlap_barrier_observed: Literal[True]


@dataclass(frozen=True)
class RaceCorpus:
    case_id: str
    uses_overlap_barrier: bool
    varied_jitter: bool
    database_constraint: str
    expected_public_outcomes: tuple[str, str]
    results: tuple[RaceSeedResult, ...]


__all__ = ["RaceCorpus", "RaceSeedResult", "jitter_pair", "race_digest"]
