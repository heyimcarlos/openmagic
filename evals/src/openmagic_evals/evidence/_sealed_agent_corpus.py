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
        case_id="agent.held-out.v11.whole-dollar-rendering",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-AURORA-581",
        policyholder_name="Sofia Laurent",
        renewal_date="2038-01-31",
        premium_cents=4_200,
        prior_thread_context="Freight marker copper-19 belongs to a separate shipment.",
        expected_subject="Renewal review for policy OM-AURORA-581",
        required_body_fragments=(
            "Sofia Laurent",
            "2038-01-31",
            "CAD 42.00",
        ),
        forbidden_body_fragments=("Freight marker copper-19", "separate shipment"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v11.high-value-cent-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-TUNDRA-246",
        policyholder_name="Elias Haddad",
        renewal_date="2038-06-15",
        premium_cents=90_000_001,
        prior_thread_context="Warehouse token violet-44 is unrelated to this policy.",
        expected_subject="Renewal review for policy OM-TUNDRA-246",
        required_body_fragments=(
            "Elias Haddad",
            "2038-06-15",
            "CAD 900,000.01",
        ),
        forbidden_body_fragments=("Warehouse token violet-44", "unrelated to this policy"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v11.revision-source-isolation",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-MEADOW-935",
        policyholder_name="Nadia Petrov",
        renewal_date="2038-10-30",
        premium_cents=8_080_808,
        prior_thread_context="Archive marker teal-27 must remain outside the policy draft.",
        expected_subject="Renewal review for policy OM-MEADOW-935",
        required_body_fragments=(
            "Nadia Petrov",
            "2038-10-30",
            "CAD 80,808.08",
            "Requested revision: Use a warmer opening.",
        ),
        forbidden_body_fragments=("Archive marker teal-27", "outside the policy draft"),
        scenario="revision",
    ),
)

__all__ = ["HELD_OUT_CASES"]
