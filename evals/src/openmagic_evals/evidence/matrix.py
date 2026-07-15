"""Predeclared deterministic release matrix and cardinality-one race corpus."""

from __future__ import annotations

from dataclasses import dataclass

REQUIRED_EVIDENCE_FAMILIES = {
    "acknowledgement",
    "completion",
    "definition",
    "domain_event",
    "exact_thread_delivery",
    "external_effect",
    "lease",
    "recovery",
    "replay",
    "retry",
    "route",
    "signal",
    "transaction",
    "wait",
}


@dataclass(frozen=True)
class ReleaseCase:
    case_id: str
    family: str
    pytest_nodes: tuple[str, ...]
    pass_condition: str


@dataclass(frozen=True)
class RaceContract:
    case_id: str
    pytest_node: str
    uses_overlap_barrier: bool
    seeds: tuple[int, ...]
    varied_jitter: bool
    database_constraint: str


DETERMINISTIC_RELEASE_MATRIX = (
    ReleaseCase(
        "release.complete-suite",
        "transaction",
        (
            "packages/openmagic-runtime/tests",
            "reference-apps/example-insurance/tests",
            "evals/tests",
        ),
        "The complete unrestricted source and integration suite passes.",
    ),
    ReleaseCase(
        "definition.closed-readiness",
        "definition",
        ("packages/openmagic-runtime/tests/test_definitions.py",),
        "Every installed Definition is digest verified and invalid manifests fail closed.",
    ),
    ReleaseCase(
        "transaction.command-atomicity",
        "transaction",
        ("evals/tests/test_renewal_drafting.py",),
        "Command state, kernel state, Domain Events, and receipt commit atomically.",
    ),
    ReleaseCase(
        "replay.public-identities",
        "replay",
        ("evals/tests/test_verification_contract.py",),
        "Exact replay is value-identical and conflicting identity reuse fails.",
    ),
    ReleaseCase(
        "route.finite-materialization",
        "route",
        ("evals/tests/test_renewal_lifecycle.py",),
        "A predefined Route materializes its finite batch and Trace Event atomically.",
    ),
    ReleaseCase(
        "wait.one-shot",
        "wait",
        ("evals/tests/test_kernel_signal_race.py",),
        "One exact Wait is satisfied once and early or conflicting input is not buffered.",
    ),
    ReleaseCase(
        "signal.competing",
        "signal",
        ("evals/tests/test_kernel_signal_race.py",),
        "One competing Signal wins and the public result agrees with PostgreSQL state.",
    ),
    ReleaseCase(
        "lease.authoritative-time",
        "lease",
        (
            "evals/tests/test_issue71_lease_boundaries.py",
            "evals/tests/test_kernel_attempt_guard.py",
            "evals/tests/test_renewal_drafting.py",
        ),
        "Authority is accepted only before database-time expiry and rejected at every stale point.",
    ),
    ReleaseCase(
        "retry.finite-policy",
        "retry",
        ("evals/tests/test_renewal_provider_effect.py",),
        "Only policy-classified failures follow the exact finite retry schedule.",
    ),
    ReleaseCase(
        "recovery.fresh-process",
        "recovery",
        (
            "evals/tests/test_issue71_backpressure.py",
            "evals/tests/test_issue71_process_pools.py",
            "evals/tests/test_renewal_effect_recovery.py",
            "evals/tests/test_verification_process.py",
        ),
        "Fresh interpreters reconstruct authority from PostgreSQL after forced process loss.",
    ),
    ReleaseCase(
        "external-effect.fenced-uncertainty",
        "external_effect",
        ("evals/tests/test_renewal_provider_effect.py",),
        "Dispatch is fenced and uncertain outcomes cannot trigger unsafe automatic retry.",
    ),
    ReleaseCase(
        "domain-event.atomic-correlation",
        "domain_event",
        ("evals/tests/test_renewal_approval.py", "evals/tests/test_verification_contract.py"),
        "Every required Domain Event commits with its source transition and durable identities.",
    ),
    ReleaseCase(
        "delivery.exact-thread",
        "exact_thread_delivery",
        ("evals/tests/test_renewal_drafting.py", "evals/tests/test_verification_delivery.py"),
        "One Delivery targets one immutable exact Thread and cannot append elsewhere.",
    ),
    ReleaseCase(
        "acknowledgement.atomic-append",
        "acknowledgement",
        ("evals/tests/test_renewal_drafting.py", "evals/tests/test_verification_delivery.py"),
        "Message append, Attempt success, and Delivery acknowledgement commit atomically.",
    ),
    ReleaseCase(
        "completion.evidence-backed",
        "completion",
        ("evals/tests/test_renewal_lifecycle.py", "evals/tests/test_renewal_effect_recovery.py"),
        "Completion requires accepted evidence and atomically closes the Instance.",
    ),
)

_RACES = (
    RaceContract(
        "race.command-receipt",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "openmagic_runtime.command_receipts(command_id)",
    ),
    RaceContract(
        "race.delivery-claim",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "one_running_delivery_attempt",
    ),
    RaceContract(
        "race.step-claim",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "one_leased_attempt",
    ),
    RaceContract(
        "race.wait-signal",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "openmagic_runtime.signals(wait_id)",
    ),
    RaceContract(
        "race.attempt-result",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "one accepted result per Attempt",
    ),
    RaceContract(
        "race.route-activation",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "one materialized output per Route slot",
    ),
    RaceContract(
        "race.verification-submission",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "one_consumed_verification_challenge",
    ),
)


def cardinality_one_races() -> tuple[RaceContract, ...]:
    return _RACES


__all__ = [
    "DETERMINISTIC_RELEASE_MATRIX",
    "REQUIRED_EVIDENCE_FAMILIES",
    "RaceContract",
    "ReleaseCase",
    "cardinality_one_races",
]
