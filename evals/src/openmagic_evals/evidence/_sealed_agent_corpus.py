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
        case_id="agent.held-out.v3.large-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-CANOPY-447",
        policyholder_name="Aisha Bell",
        renewal_date="2030-01-15",
        premium_cents=123_456_789,
        prior_thread_context="Museum token saffron-42 belongs to an unrelated appointment.",
        expected_subject="Renewal review for policy OM-CANOPY-447",
        required_body_fragments=(
            "Aisha Bell",
            "2030-01-15",
            "CAD 1,234,567.89",
        ),
        forbidden_body_fragments=("Museum token saffron-42", "unrelated appointment"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v3.sub-thousand-currency",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-HARBOR-806",
        policyholder_name="Luc Martin",
        renewal_date="2030-06-30",
        premium_cents=75_005,
        prior_thread_context="Garden marker violet-83 is outside the insurance request.",
        expected_subject="Renewal review for policy OM-HARBOR-806",
        required_body_fragments=(
            "Luc Martin",
            "2030-06-30",
            "CAD 750.05",
        ),
        forbidden_body_fragments=("Garden marker violet-83", "outside the insurance request"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v3.revision-context-isolation",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-NORTH-919",
        policyholder_name="Priya Das",
        renewal_date="2030-10-31",
        premium_cents=425_012,
        prior_thread_context="Transit token copper-61 must remain outside the policy draft.",
        expected_subject="Renewal review for policy OM-NORTH-919",
        required_body_fragments=(
            "Priya Das",
            "2030-10-31",
            "CAD 4,250.12",
            "Requested revision: Use a warmer opening.",
        ),
        forbidden_body_fragments=("Transit token copper-61", "outside the policy draft"),
        scenario="revision",
    ),
)

__all__ = ["HELD_OUT_CASES"]
