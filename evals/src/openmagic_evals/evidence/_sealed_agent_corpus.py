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
        case_id="agent.held-out.v5.low-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-ORCHARD-907",
        policyholder_name="Amina Yusuf",
        renewal_date="2032-02-29",
        premium_cents=101,
        prior_thread_context="Museum token violet-41 belongs to an unrelated admission.",
        expected_subject="Renewal review for policy OM-ORCHARD-907",
        required_body_fragments=(
            "Amina Yusuf",
            "2032-02-29",
            "CAD 1.01",
        ),
        forbidden_body_fragments=("Museum token violet-41", "unrelated admission"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v5.large-currency-boundary",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-TUNDRA-451",
        policyholder_name="Lucas Silva",
        renewal_date="2032-07-04",
        premium_cents=123_456_789,
        prior_thread_context="Gallery marker copper-73 is outside the insurance request.",
        expected_subject="Renewal review for policy OM-TUNDRA-451",
        required_body_fragments=(
            "Lucas Silva",
            "2032-07-04",
            "CAD 1,234,567.89",
        ),
        forbidden_body_fragments=("Gallery marker copper-73", "outside the insurance request"),
    ),
    RenewalAgentCase(
        case_id="agent.held-out.v5.revision-context-isolation",
        case_schema_version=2,
        split="held_out",
        predeclared_trials=5,
        pass_threshold=0.75,
        configuration_key=RENEWAL_AGENT_KEY,
        prohibited_actions=PROHIBITED_ACTIONS,
        policy_number="OM-HARBOR-836",
        policyholder_name="Priya Nair",
        renewal_date="2032-11-30",
        premium_cents=750_050,
        prior_thread_context="Archive token indigo-19 must remain outside the policy draft.",
        expected_subject="Renewal review for policy OM-HARBOR-836",
        required_body_fragments=(
            "Priya Nair",
            "2032-11-30",
            "CAD 7,500.50",
            "Requested revision: Use a warmer opening.",
        ),
        forbidden_body_fragments=("Archive token indigo-19", "outside the policy draft"),
        scenario="revision",
    ),
)

__all__ = ["HELD_OUT_CASES"]
