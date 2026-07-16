"""Versioned held-out Agent corpus sealed before any subsequent tuning."""

from __future__ import annotations

from openmagic_evals.evidence._sealed_agent_corpus import HELD_OUT_CASES

HELD_OUT_CORPUS_VERSION = "issue-71.agent-heldout.v2"
HELD_OUT_SEALED_AT_COMMIT = "304349ae02c53a4db08641fa22c5384c2783ca5e"
HELD_OUT_SEALED_BLOB = "12bd63dfeb37f0ba805cfdcfdc2e63a55941174b"
HELD_OUT_SEALED_PATH = "evals/src/openmagic_evals/evidence/_sealed_agent_corpus.py"
HELD_OUT_CORPUS_DIGEST = "sha256:8c6e84ad1386446e4777f692339e06e8feb6ee79d67f452f4bc791bfc3850634"
TUNING_LOCKED_BLOBS = {
    "packages/openmagic-runtime/src/openmagic_runtime/agents.py": "af60373bcedb7bc0ebeb063caeb71881dcfa2777",
    "packages/openmagic-runtime/src/openmagic_runtime/commands.py": "71876d561037769fcc86630673e1e9e15c773d68",
    "packages/openmagic-runtime/src/openmagic_runtime/execution.py": "4e98a57f0f8674fc4a71f7a855ebbbfaba49be77",
    "packages/openmagic-runtime/src/openmagic_runtime/kernel/work.py": "ddb21b39203d262dbe6e0bd03bc5602100f38eef",
    "packages/openmagic-runtime/src/openmagic_runtime/threads.py": "89a94ac60aa13ddcd216164384340f96978976b1",
    "reference-apps/example-insurance/src/example_insurance/renewal_attempt_control.py": "1f40f29463c661f889027280bdf5f315bd61f6d5",
    "reference-apps/example-insurance/src/example_insurance/renewal_attempts.py": "8b047c27df50b9c42464e4261aa7c074db1b2215",
    "reference-apps/example-insurance/src/example_insurance/renewal_commands.py": "4369b751ac6ec623180643a33f106fcb666ca958",
    "reference-apps/example-insurance/src/example_insurance/renewal_definition.py": "521018319e6bb2f1939864ed57f137a8d25d3b2b",
    "reference-apps/example-insurance/src/example_insurance/renewal_evidence.py": "c9eff2180cb2d3fb3ad6502f4d1902da8f4df12f",
    "reference-apps/example-insurance/src/example_insurance/renewal_facts.py": "1ecf883461ebf93c858b0cb21ac3a01f27c7f974",
    "reference-apps/example-insurance/src/example_insurance/renewals.py": "de9afd276321c5939af7d317ddb9d92e140d72fd",
    "reference-apps/example-insurance/src/example_insurance/workflow_worker_control.py": "6e618ff9dea9f3502e71ee0984a50b326b51d120",
    "apps/playground/src/openmagic_playground/renewal_observation.py": "6239a76fa7d8dfed450895ef2fccd6de3384c0ee",
    "evals/src/openmagic_evals/evidence/agent_boundary_trials.py": "314cfd3bbcbb50424a7f140b5821cb2eea32912a",
    "evals/src/openmagic_evals/evidence/agent_cases.py": "4e6a71536e07f39a1a959e08c0ebc8170d353e99",
    "evals/src/openmagic_evals/evidence/agent_models.py": "282759e90f760694095dd38996a406216f9646e4",
    "evals/src/openmagic_evals/evidence/agent_quality.py": "e7103bcf4d4bebb146913cae413cafd54ab8d059",
    "evals/src/openmagic_evals/evidence/agent_scoring.py": "ed37d032bfecd6f3731cd24c038574f60f7ed8ed",
    "evals/src/openmagic_evals/evidence/agent_trials.py": "a046db5d57d7ac0b0dfbf133ed0cd3c8c4c20afb",
    "evals/src/openmagic_evals/evidence/contracts.py": "ed377e6c7f0604e660c6bd84a884ad11088d7efd",
    "evals/src/openmagic_evals/evidence/core_models.py": "917950e5efae90377301dace35ab08994da94841",
    "evals/src/openmagic_evals/evidence/inspection.py": "057f32f97e20066020a3d990db6627f35fa4ca8d",
    "evals/src/openmagic_evals/harness/renewal_scenario.py": "b9010797e6cd101e0a239942a4367fbccbc29e0a",
}
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
