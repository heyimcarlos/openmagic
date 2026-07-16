"""Versioned held-out Agent corpus sealed before any subsequent tuning."""

from __future__ import annotations

from openmagic_evals.evidence._sealed_agent_corpus import HELD_OUT_CASES

HELD_OUT_CORPUS_VERSION = "issue-71.agent-heldout.v2"
HELD_OUT_SEALED_AT_COMMIT = "792c9bbf165af9e423fa1986423381de6854175a"
HELD_OUT_SEALED_BLOB = "12bd63dfeb37f0ba805cfdcfdc2e63a55941174b"
HELD_OUT_SEALED_PATH = "evals/src/openmagic_evals/evidence/_sealed_agent_corpus.py"
HELD_OUT_CORPUS_DIGEST = "sha256:8c6e84ad1386446e4777f692339e06e8feb6ee79d67f452f4bc791bfc3850634"
TUNING_LOCKED_BLOBS = dict.fromkeys(
    (
        "packages/openmagic-runtime/src/openmagic_runtime/agents.py",
        "packages/openmagic-runtime/src/openmagic_runtime/commands.py",
        "packages/openmagic-runtime/src/openmagic_runtime/execution.py",
        "packages/openmagic-runtime/src/openmagic_runtime/kernel/work.py",
        "packages/openmagic-runtime/src/openmagic_runtime/threads.py",
        "reference-apps/example-insurance/src/example_insurance/renewal_attempt_control.py",
        "reference-apps/example-insurance/src/example_insurance/renewal_attempts.py",
        "reference-apps/example-insurance/src/example_insurance/renewal_commands.py",
        "reference-apps/example-insurance/src/example_insurance/renewal_definition.py",
        "reference-apps/example-insurance/src/example_insurance/renewal_evidence.py",
        "reference-apps/example-insurance/src/example_insurance/renewal_facts.py",
        "reference-apps/example-insurance/src/example_insurance/renewals.py",
        "reference-apps/example-insurance/src/example_insurance/workflow_worker_control.py",
        "apps/playground/src/openmagic_playground/renewal_observation.py",
        "evals/src/openmagic_evals/evidence/agent_boundary_trials.py",
        "evals/src/openmagic_evals/evidence/agent_cases.py",
        "evals/src/openmagic_evals/evidence/agent_models.py",
        "evals/src/openmagic_evals/evidence/agent_quality.py",
        "evals/src/openmagic_evals/evidence/agent_scoring.py",
        "evals/src/openmagic_evals/evidence/agent_trials.py",
        "evals/src/openmagic_evals/evidence/contracts.py",
        "evals/src/openmagic_evals/evidence/core_models.py",
        "evals/src/openmagic_evals/evidence/inspection.py",
        "evals/src/openmagic_evals/harness/renewal_scenario.py",
    ),
    "0" * 40,
)
TUNING_LOCKED_PATHS = tuple(TUNING_LOCKED_BLOBS)

__all__ = [
    "HELD_OUT_CASES",
    "HELD_OUT_CORPUS_DIGEST",
    "HELD_OUT_CORPUS_VERSION",
    "HELD_OUT_SEALED_AT_COMMIT",
    "HELD_OUT_SEALED_BLOB",
    "HELD_OUT_SEALED_PATH",
    "TUNING_LOCKED_BLOBS",
    "TUNING_LOCKED_PATHS",
]
