"""Verified held-out corpus loading and immutable seal metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openmagic_evals.evidence.agent_cases import AgentCase


@dataclass(frozen=True)
class HeldOutCorpusPhase:
    cases: tuple[AgentCase, ...]
    corpus_version: str
    corpus_digest: str
    sealed_at_commit: str
    runner_frozen_at_commit: str
    tuning_locked_roots: tuple[str, ...]
    tuning_locked_source_digest: str


def load_verified_held_out_corpus(repository_root: Path) -> HeldOutCorpusPhase:
    """Verify the freeze-before-exposure seal before returning held-out cases."""

    from openmagic_evals.evidence.sealed_holdout import (
        HELD_OUT_CASES,
        HELD_OUT_CORPUS_DIGEST,
        HELD_OUT_CORPUS_VERSION,
        HELD_OUT_SEALED_AT_COMMIT,
        RUNNER_FROZEN_AT_COMMIT,
        TUNING_LOCKED_ROOTS,
        TUNING_LOCKED_SOURCE_DIGEST,
        verify_held_out_seal,
    )

    verify_held_out_seal(repository_root.resolve())
    return HeldOutCorpusPhase(
        cases=HELD_OUT_CASES,
        corpus_version=HELD_OUT_CORPUS_VERSION,
        corpus_digest=HELD_OUT_CORPUS_DIGEST,
        sealed_at_commit=HELD_OUT_SEALED_AT_COMMIT,
        runner_frozen_at_commit=RUNNER_FROZEN_AT_COMMIT,
        tuning_locked_roots=TUNING_LOCKED_ROOTS,
        tuning_locked_source_digest=TUNING_LOCKED_SOURCE_DIGEST,
    )


__all__ = ["HeldOutCorpusPhase", "load_verified_held_out_corpus"]
