"""Versioned held-out Agent corpus sealed before any subsequent tuning."""

from __future__ import annotations

from openmagic_evals.evidence._sealed_agent_corpus import HELD_OUT_CASES

HELD_OUT_CORPUS_VERSION = "issue-71.agent-heldout.v2"
HELD_OUT_SEALED_AT_COMMIT = "792c9bbf165af9e423fa1986423381de6854175a"
HELD_OUT_SEALED_BLOB = "12bd63dfeb37f0ba805cfdcfdc2e63a55941174b"
HELD_OUT_SEALED_PATH = "evals/src/openmagic_evals/evidence/_sealed_agent_corpus.py"
HELD_OUT_CORPUS_DIGEST = "sha256:8c6e84ad1386446e4777f692339e06e8feb6ee79d67f452f4bc791bfc3850634"
TUNING_LOCKED_PATHS = (
    "packages/openmagic-runtime/src/openmagic_runtime/agents.py",
    "packages/openmagic-runtime/src/openmagic_runtime/execution.py",
    "reference-apps/example-insurance/src/example_insurance/renewal_attempts.py",
    "reference-apps/example-insurance/src/example_insurance/renewals.py",
    "reference-apps/example-insurance/src/example_insurance/workflow_worker_control.py",
    "evals/src/openmagic_evals/evidence/agent_boundary_trials.py",
    "evals/src/openmagic_evals/evidence/agent_cases.py",
    "evals/src/openmagic_evals/evidence/agent_scoring.py",
    "evals/src/openmagic_evals/evidence/agent_trials.py",
)

__all__ = [
    "HELD_OUT_CASES",
    "HELD_OUT_CORPUS_DIGEST",
    "HELD_OUT_CORPUS_VERSION",
    "HELD_OUT_SEALED_AT_COMMIT",
    "HELD_OUT_SEALED_BLOB",
    "HELD_OUT_SEALED_PATH",
    "TUNING_LOCKED_PATHS",
]
