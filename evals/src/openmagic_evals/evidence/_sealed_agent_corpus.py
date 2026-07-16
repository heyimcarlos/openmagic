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
        case_id="agent.held-out.v6.low-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-MAPLE-275",
        policyholder_name="Sofia Alvarez",
        renewal_date="2033-01-15",
        premium_cents=4_075,
        prior_thread_context="Transit token crimson-52 belongs to an unrelated journey.",
        expected_subject="Renewal review for policy OM-MAPLE-275",
        required_body_fragments=(
            "Sofia Alvarez",
            "2033-01-15",
            "CAD 40.75",
        ),
        forbidden_body_fragments=("Transit token crimson-52", "unrelated journey"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v6.large-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-SUMMIT-904",
        policyholder_name="Daniel Okafor",
        renewal_date="2033-06-30",
        premium_cents=87_654_321,
        prior_thread_context="Studio marker saffron-84 is outside the insurance request.",
        expected_subject="Renewal review for policy OM-SUMMIT-904",
        required_body_fragments=(
            "Daniel Okafor",
            "2033-06-30",
            "CAD 876,543.21",
        ),
        forbidden_body_fragments=("Studio marker saffron-84", "outside the insurance request"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v6.revision-context-isolation",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-CEDAR-518",
        policyholder_name="Hana Kim",
        renewal_date="2033-10-09",
        premium_cents=1_250_000,
        prior_thread_context="Festival token cobalt-36 must remain outside the policy draft.",
        expected_subject="Renewal review for policy OM-CEDAR-518",
        required_body_fragments=(
            "Hana Kim",
            "2033-10-09",
            "CAD 12,500.00",
            "Requested revision: Use a warmer opening.",
        ),
        forbidden_body_fragments=("Festival token cobalt-36", "outside the policy draft"),
        scenario="revision",
    ),
)

__all__ = ["HELD_OUT_CASES"]
