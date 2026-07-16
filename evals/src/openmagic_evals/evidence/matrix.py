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
    "executor",
    "lease",
    "recovery",
    "replay",
    "retry",
    "route",
    "signal",
    "transaction",
    "trace_completeness",
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
    expected_public_outcomes: tuple[str, str]


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
        (
            "packages/openmagic-runtime/tests/test_definitions.py::test_closed_definition_accepts_acyclic_exact_contracts",
            "packages/openmagic-runtime/tests/test_definitions.py::test_closed_definition_rejects_cycles_and_kind_mismatches",
        ),
        "Every installed Definition is digest verified and invalid manifests fail closed.",
    ),
    ReleaseCase(
        "transaction.command-atomicity",
        "transaction",
        (
            "evals/tests/test_renewal_drafting.py::test_start_command_commits_and_replays_value_identically",
            "evals/tests/test_renewal_drafting.py::test_command_validation_rejects_nested_types_and_semantics_before_commit",
        ),
        "Valid Command state, kernel state, Domain Events, and receipt commit together; invalid input does not commit and exact replay is value-identical.",
    ),
    ReleaseCase(
        "replay.public-identities",
        "replay",
        (
            "evals/tests/test_verification_contract.py::test_verification_code_is_single_use_replay_safe_and_serialized",
        ),
        "Exact replay is value-identical and conflicting identity reuse fails.",
    ),
    ReleaseCase(
        "route.finite-materialization",
        "route",
        (
            "evals/tests/test_renewal_drafting.py::test_start_route_replay_returns_the_same_complete_occurrence_batch",
        ),
        "A predefined Route materializes one complete finite batch and exact replay returns the same occurrences.",
    ),
    ReleaseCase(
        "wait.one-shot",
        "wait",
        (
            "evals/tests/test_renewal_approval.py::test_exact_approval_satisfies_one_wait_and_materializes_the_fenced_email_step",
        ),
        "One exact Wait is satisfied once and early or conflicting input is not buffered.",
    ),
    ReleaseCase(
        "signal.competing",
        "signal",
        (
            "evals/tests/test_kernel_signal_race.py::test_competing_signals_have_one_winner_in_100_seeded_real_transaction_races",
        ),
        "One competing Signal wins and the public result agrees with PostgreSQL state.",
    ),
    ReleaseCase(
        "lease.authoritative-time",
        "lease",
        (
            "evals/tests/test_issue71_lease_boundaries.py::test_lease_authority_boundaries_use_database_time_without_a_grace_period",
            "evals/tests/test_kernel_attempt_guard.py::test_current_attempt_guard_rejects_expired_abandoned_and_superseded_authority",
        ),
        "Authority is accepted only before database-time expiry and rejected at every stale point.",
    ),
    ReleaseCase(
        "retry.finite-policy",
        "retry",
        (
            "evals/tests/test_renewal_provider_effect.py::test_definite_non_application_retries_the_same_effect_identity_then_completes",
        ),
        "Only policy-classified failures follow the exact finite retry schedule.",
    ),
    ReleaseCase(
        "recovery.fresh-process",
        "recovery",
        (
            "evals/tests/test_issue71_backpressure.py::test_separate_process_pools_drain_backpressure_after_forced_loss",
            "evals/tests/test_renewal_effect_recovery.py::test_fresh_process_loss_during_provider_io_reconciles_without_redispatch",
        ),
        "Fresh interpreters reconstruct authority from PostgreSQL after forced process loss.",
    ),
    ReleaseCase(
        "external-effect.fenced-uncertainty",
        "external_effect",
        (
            "evals/tests/test_renewal_provider_effect.py::test_response_loss_defers_email_retry_until_fresh_provider_reconciliation",
        ),
        "Dispatch is fenced and uncertain outcomes cannot trigger unsafe automatic retry.",
    ),
    ReleaseCase(
        "executor.typed-malformed-timeout",
        "executor",
        (
            "packages/openmagic-runtime/tests/test_execution.py::test_fresh_agent_executor_returns_only_its_typed_candidate",
            "packages/openmagic-runtime/tests/test_execution.py::test_fresh_agent_executor_rejects_malformed_candidate_type",
            "packages/openmagic-runtime/tests/test_execution.py::test_fresh_agent_executor_terminates_work_after_timeout",
            "evals/tests/test_renewal_drafting.py::test_agent_process_loss_terminalizes_run_and_retries_without_phantom_authority",
        ),
        "Malformed, timed-out, and lost Agent execution cannot publish an invalid result.",
    ),
    ReleaseCase(
        "domain-event.atomic-correlation",
        "domain_event",
        (
            "evals/tests/test_renewal_drafting.py::test_one_domain_event_can_create_multiple_exact_destination_delivery_obligations",
        ),
        "Every required Domain Event commits with its source transition and durable identities.",
    ),
    ReleaseCase(
        "delivery.exact-thread",
        "exact_thread_delivery",
        (
            "evals/tests/test_renewal_drafting.py::test_delivery_appends_once_to_only_the_frozen_exact_thread",
        ),
        "One Delivery targets one immutable exact Thread and cannot append elsewhere.",
    ),
    ReleaseCase(
        "acknowledgement.atomic-append",
        "acknowledgement",
        (
            "evals/tests/test_renewal_drafting.py::test_delivery_appends_once_to_only_the_frozen_exact_thread",
            "evals/tests/test_renewal_drafting.py::test_delivery_process_loss_after_claim_recovers_without_duplicate_message",
        ),
        "Message append and Delivery acknowledgement recover after process loss without a duplicate Message.",
    ),
    ReleaseCase(
        "completion.evidence-backed",
        "completion",
        (
            "evals/tests/test_renewal_provider_effect.py::test_successful_provider_evidence_completes_and_closes_the_instance",
        ),
        "Completion requires accepted evidence and atomically closes the Instance.",
    ),
    ReleaseCase(
        "trace.complete-durable-chain",
        "trace_completeness",
        (
            "evals/tests/test_verification_evidence.py::test_agent_and_deterministic_workflows_share_runtime_attempt_evidence",
        ),
        "Public renewal and verification projections link every accepted durable identity.",
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
        ("value_identical_receipt", "value_identical_receipt"),
    ),
    RaceContract(
        "race.delivery-claim",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "one_running_delivery_attempt",
        ("claimed", "not_claimed"),
    ),
    RaceContract(
        "race.step-claim",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "one_leased_attempt_per_step",
        ("claimed", "not_claimed"),
    ),
    RaceContract(
        "race.wait-signal",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "openmagic_runtime.signals(wait_id)",
        ("accepted", "conflict"),
    ),
    RaceContract(
        "race.attempt-result",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "one accepted result per Attempt",
        ("accepted", "replayed"),
    ),
    RaceContract(
        "race.route-activation",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "one materialized output per Route slot",
        ("value_identical_receipt", "value_identical_receipt"),
    ),
    RaceContract(
        "race.verification-submission",
        "evals/tests/test_issue71_race_corpus.py::test_all_cardinality_races_record_actual_trials",
        True,
        tuple(range(100)),
        True,
        "example_insurance.verification_sessions(challenge_id)",
        ("already_used", "verified"),
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
