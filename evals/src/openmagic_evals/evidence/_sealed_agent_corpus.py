"""Immutable versioned held-out Agent cases, separate from their seal metadata."""

from __future__ import annotations

from openmagic_evals.evidence.agent_cases import (
    PROHIBITED_ACTIONS,
    RENEWAL_AGENT_KEY,
    AgentCase,
    RenewalAgentCase,
)

HELD_OUT_CASES: tuple[AgentCase, ...] = (
    RenewalAgentCase(
        case_id="agent.held-out.v8.exact-dollar-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-GLACIER-314",
        policyholder_name="Elena Petrov",
        renewal_date="2035-03-17",
        premium_cents=780_000,
        prior_thread_context="Parcel token copper-68 belongs to an unrelated shipment.",
        expected_subject="Renewal review for policy OM-GLACIER-314",
        required_body_fragments=(
            "Elena Petrov",
            "2035-03-17",
            "CAD 7,800.00",
        ),
        forbidden_body_fragments=("Parcel token copper-68", "unrelated shipment"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v8.six-figure-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-ATLANTIC-926",
        policyholder_name="Kwame Mensah",
        renewal_date="2035-08-22",
        premium_cents=10_000_099,
        prior_thread_context="Museum marker violet-57 is outside the insurance request.",
        expected_subject="Renewal review for policy OM-ATLANTIC-926",
        required_body_fragments=(
            "Kwame Mensah",
            "2035-08-22",
            "CAD 100,000.99",
        ),
        forbidden_body_fragments=("Museum marker violet-57", "outside the insurance request"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v8.revision-context-isolation",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-AURORA-457",
        policyholder_name="Mei Wong",
        renewal_date="2035-12-05",
        premium_cents=67_890,
        prior_thread_context="Library token silver-12 must remain outside the policy draft.",
        expected_subject="Renewal review for policy OM-AURORA-457",
        required_body_fragments=(
            "Mei Wong",
            "2035-12-05",
            "CAD 678.90",
            "Requested revision: Use a warmer opening.",
        ),
        forbidden_body_fragments=("Library token silver-12", "outside the policy draft"),
        scenario="revision",
    ),
)

__all__ = ["HELD_OUT_CASES"]
